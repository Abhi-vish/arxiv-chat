import json
from contextlib import asynccontextmanager

import httpx
import pytest

from backend.src.config import Settings
from backend.src.ingestion.arxiv import FetchFailed, PaperNotFound
from backend.src.ingestion.pipeline import IngestionPipeline
from backend.src.llm import LLMError
from backend.src.main import Container, RequestLimiter, create_app
from backend.src.store import Store
from backend.tests.test_pipeline import KEY, META, URL, FakeClient, ok_parse


class FakeStrategy:
    def __init__(self, chunks=("Hel", "lo\nworld"), exc=None):
        self.chunks, self.exc, self.calls = chunks, exc, []

    async def answer(self, papers, question, history):
        self.calls.append((papers, question, list(history)))
        for c in self.chunks:
            yield c
        if self.exc:
            raise self.exc


@asynccontextmanager
async def api(tmp_path, *, client=None, strategy=None, **settings):
    store = Store(tmp_path / "t.db")
    fake_client = client or FakeClient()
    strategy = strategy or FakeStrategy()
    container = Container(store, IngestionPipeline(fake_client, store, parse=ok_parse), strategy)
    s = Settings(gemini_api_key="sk-test", _env_file=None, **settings)
    app = create_app(container, s)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, container, fake_client, strategy


def parse_sse(text: str):
    out = []
    for block in text.strip().split("\n\n"):
        name_line, data_line = block.split("\n")
        out.append((name_line.removeprefix("event: "),
                    json.loads(data_line.removeprefix("data: "))))
    return out


async def ready_paper(http):
    r = await http.post("/papers", json={"url": URL})
    assert r.status_code == 202
    return r.json()["cache_key"]


# papers

@pytest.mark.asyncio
async def test_health(tmp_path):
    async with api(tmp_path) as (http, *_):
        assert (await http.get("/health")).json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_submit_then_poll_then_cache_hit(tmp_path):
    async with api(tmp_path) as (http, _, client, _s):
        r = await http.post("/papers", json={"url": URL})
        assert r.status_code == 202
        assert r.json()["status"] == "queued" and r.json()["cache_key"] == KEY
        assert r.json()["arxiv_id"] == "2401.12345v2"

        # background task finished by the time the response completed
        polled = await http.get(f"/papers/{KEY}")
        assert polled.json()["status"] == "ready" and polled.json()["source_kind"] == "html"

        again = await http.post("/papers", json={"url": URL})
        assert again.status_code == 200 and again.json()["status"] == "ready"
        assert client.content_calls == 1


@pytest.mark.asyncio
async def test_invalid_url_400(tmp_path):
    async with api(tmp_path) as (http, *_):
        r = await http.post("/papers", json={"url": "https://example.com/x"})
        assert r.status_code == 400 and "Invalid arXiv link" in r.json()["detail"]


@pytest.mark.asyncio
async def test_paper_not_found_404(tmp_path):
    async with api(tmp_path, client=FakeClient(meta_exc=PaperNotFound("x"))) as (http, *_):
        assert (await http.post("/papers", json={"url": URL})).status_code == 404


@pytest.mark.asyncio
async def test_arxiv_down_502_without_detail_leak(tmp_path):
    client = FakeClient(meta_exc=FetchFailed("https://secret-host: HTTP 503"))
    async with api(tmp_path, client=client) as (http, *_):
        r = await http.post("/papers", json={"url": URL})
        assert r.status_code == 502 and "secret-host" not in r.text


@pytest.mark.asyncio
async def test_status_unknown_404(tmp_path):
    async with api(tmp_path) as (http, *_):
        assert (await http.get("/papers/nope")).status_code == 404
        assert (await http.get("/papers/%27%3B%20DROP%20TABLE")).status_code == 404


@pytest.mark.asyncio
async def test_ingest_failure_visible_in_status(tmp_path):
    client = FakeClient(content_exc=FetchFailed("boom"))
    async with api(tmp_path, client=client) as (http, *_):
        key = await ready_paper(http)
        s = (await http.get(f"/papers/{key}")).json()
        assert s["status"] == "failed" and "boom" not in s["error"]


# chat

@pytest.mark.asyncio
async def test_chat_streams_tokens_then_done(tmp_path):
    async with api(tmp_path) as (http, _, _c, strategy):
        key = await ready_paper(http)
        body = {"question": "  Why?  ",
                "history": [{"role": "user", "content": "q1"},
                            {"role": "assistant", "content": "a1"}]}
        r = await http.post(f"/papers/{key}/chat", json=body)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(r.text)
        assert [e[0] for e in events] == ["token", "token", "done"]
        assert "".join(e[1]["text"] for e in events[:-1]) == "Hello\nworld"  # newline survives

        papers, question, history = strategy.calls[0]
        assert question == "Why?"                      # stripped
        assert [t.content for t in history] == ["q1", "a1"]
        assert papers[0].markdown.startswith("# Paper")


@pytest.mark.asyncio
async def test_chat_unknown_paper_404(tmp_path):
    async with api(tmp_path) as (http, *_):
        r = await http.post("/papers/nope/chat", json={"question": "hi"})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_chat_while_processing_409(tmp_path):
    async with api(tmp_path) as (http, c, *_):
        await c.store.claim(META)                       # row stays 'queued'
        r = await http.post(f"/papers/{KEY}/chat", json={"question": "hi"})
        assert r.status_code == 409 and "processing" in r.json()["detail"]


@pytest.mark.asyncio
async def test_chat_on_failed_paper_409_with_reason(tmp_path):
    async with api(tmp_path) as (http, c, *_):
        await c.store.claim(META)
        await c.store.mark_failed(KEY, "This paper is too large to process.")
        r = await http.post(f"/papers/{KEY}/chat", json={"question": "hi"})
        assert r.status_code == 409 and "too large" in r.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"question": ""},
    {"question": "   "},
    {"question": "x" * 2001},
    {"question": "ok", "history": [{"role": "system", "content": "evil"}]},
    {"question": "ok", "history": [{"role": "user", "content": "x"}] * 51},
    {},
])
async def test_chat_validation_422(tmp_path, body):
    async with api(tmp_path) as (http, c, *_):
        await c.store.claim(META)
        r = await http.post(f"/papers/{KEY}/chat", json=body)
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_midstream_llm_error_becomes_error_event(tmp_path):
    strat = FakeStrategy(chunks=("par",), exc=LLMError("The language model request failed."))
    async with api(tmp_path, strategy=strat) as (http, *_):
        key = await ready_paper(http)
        r = await http.post(f"/papers/{key}/chat", json={"question": "hi"})
        assert r.status_code == 200
        events = parse_sse(r.text)
        assert [e[0] for e in events] == ["token", "error"]
        assert "language model" in events[1][1]["message"]


@pytest.mark.asyncio
async def test_unexpected_stream_error_is_generic(tmp_path):
    strat = FakeStrategy(chunks=(), exc=RuntimeError("secret internals"))
    async with api(tmp_path, strategy=strat) as (http, *_):
        key = await ready_paper(http)
        r = await http.post(f"/papers/{key}/chat", json={"question": "hi"})
        assert "secret" not in r.text and parse_sse(r.text)[0][0] == "error"


# rate limits

@pytest.mark.asyncio
async def test_chat_rate_limit(tmp_path):
    async with api(tmp_path, chat_rate_per_min=2) as (http, *_):
        key = await ready_paper(http)
        for _ in range(2):
            assert (await http.post(f"/papers/{key}/chat", json={"question": "hi"})).status_code == 200
        r = await http.post(f"/papers/{key}/chat", json={"question": "hi"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_limiter_window_slides():
    t = [0.0]
    rl = RequestLimiter(2, window=60, clock=lambda: t[0])
    assert rl.check("a") is None and rl.check("a") is None
    assert rl.check("a") == 60.0
    assert rl.check("b") is None
    t[0] = 61
    assert rl.check("a") is None