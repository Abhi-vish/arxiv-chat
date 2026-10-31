import asyncio

import pytest

from backend.src.ingestion.arxiv import (
    ArxivId,
    FetchedContent,
    FetchFailed,
    InvalidArxivUrl,
    PaperMetadata,
    PaperNotFound,
)
from backend.src.ingestion.parser import ParsedPaper, ParseFailed
from backend.src.ingestion.pipeline import IngestionPipeline
from backend.src.store import Store
from backend.src.llm import LLMError

URL = "https://arxiv.org/abs/2401.12345"
META = PaperMetadata(ArxivId("2401.12345", 2), "T", ["A"], "Abs")
KEY = META.id.cache_key  # "2401.12345v2"


class FakeClient:
    def __init__(self, *, meta_exc=None, content_exc=None, store=None):
        self.meta_exc, self.content_exc, self.store = meta_exc, content_exc, store
        self.metadata_calls = 0
        self.content_calls = 0
        self.status_during_fetch: list[str] = []

    async def fetch_metadata(self, _id):
        self.metadata_calls += 1
        if self.meta_exc:
            raise self.meta_exc
        return META

    async def fetch_content(self, _id):
        self.content_calls += 1
        if self.store:
            self.status_during_fetch.append((await self.store.get(KEY)).status)
        if self.content_exc:
            raise self.content_exc
        return FetchedContent("html", b"<html/>", "u")


def ok_parse(_c):
    return ParsedPaper("# Paper\n" * 100, "html")


def bad_parse(_c):
    raise ParseFailed("only 3 chars extracted (scanned?)")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def make_counter(n=1000, exc=None):
    seen: list[str] = []

    async def count(text: str) -> int:
        seen.append(text)
        if exc:
            raise exc
        return n

    count.seen = seen  # type: ignore[attr-defined]
    return count


def make(store, client=None, parse=ok_parse, count=None, **kw):
    client = client or FakeClient(store=store)
    count = count or make_counter()
    return IngestionPipeline(client, store, parse=parse, count_tokens=count, **kw), client


@pytest.mark.asyncio
async def test_happy_path(store):
    p, client = make(store)
    sub = await p.submit(URL)
    assert sub.started and sub.record.status == "queued"
    assert sub.record.cache_key == KEY            # version resolved from metadata

    await p.run(KEY)
    rec = await store.get(KEY)
    assert rec.status == "ready" and rec.source_kind == "html"
    assert (await store.get_markdown(KEY)).startswith("# Paper")
    assert client.status_during_fetch == ["fetching"]


@pytest.mark.asyncio
async def test_ready_paper_is_cache_hit(store):
    p, client = make(store)
    await p.submit(URL)
    await p.run(KEY)
    sub = await p.submit(URL)
    assert sub.started is False and sub.record.status == "ready"
    assert client.content_calls == 1


@pytest.mark.asyncio
async def test_concurrent_submits_have_single_winner(store):
    p, _ = make(store)
    subs = await asyncio.gather(*(p.submit(URL) for _ in range(5)))
    assert sum(s.started for s in subs) == 1


@pytest.mark.asyncio
async def test_in_progress_not_restarted(store):
    p, _ = make(store)
    assert (await p.submit(URL)).started is True
    assert (await p.submit(URL)).started is False


@pytest.mark.asyncio
async def test_parse_failure_then_retry_succeeds(store):
    p, _ = make(store, parse=bad_parse)
    await p.submit(URL)
    await p.run(KEY)
    rec = await store.get(KEY)
    assert rec.status == "failed"
    assert "extract" in rec.error and "scanned" not in rec.error  # detail stays in logs

    p2, _ = make(store, parse=ok_parse)
    sub = await p2.submit(URL)
    assert sub.started is True
    await p2.run(KEY)
    assert (await store.get(KEY)).status == "ready"


@pytest.mark.asyncio
async def test_fetch_failure_is_user_safe(store):
    p, _ = make(store, FakeClient(content_exc=FetchFailed("https://x: HTTP 503")))
    await p.submit(URL)
    await p.run(KEY)
    rec = await store.get(KEY)
    assert rec.status == "failed" and "download" in rec.error and "503" not in rec.error


@pytest.mark.asyncio
async def test_unexpected_error_hidden_from_user(store):
    p, _ = make(store, FakeClient(content_exc=RuntimeError("secret detail")))
    await p.submit(URL)
    await p.run(KEY)                                  # must not raise
    rec = await store.get(KEY)
    assert rec.status == "failed" and "secret" not in rec.error


@pytest.mark.asyncio
async def test_too_large_fails(store):
    p, _ = make(store, max_chars=10)
    await p.submit(URL)
    await p.run(KEY)
    rec = await store.get(KEY)
    assert rec.status == "failed" and "too large" in rec.error
    assert await store.get_markdown(KEY) is None


@pytest.mark.asyncio
async def test_invalid_url_makes_no_network_call(store):
    p, client = make(store)
    with pytest.raises(InvalidArxivUrl):
        await p.submit("nope")
    assert client.metadata_calls == 0


@pytest.mark.asyncio
async def test_not_found_propagates_and_stores_nothing(store):
    p, _ = make(store, FakeClient(meta_exc=PaperNotFound("x")))
    with pytest.raises(PaperNotFound):
        await p.submit(URL)
    assert await store.get(KEY) is None


@pytest.mark.asyncio
async def test_run_unknown_key_is_noop(store):
    p, _ = make(store)
    await p.run("nope")

@pytest.mark.asyncio
async def test_token_count_stored_and_counter_sees_markdown(store):
    count = make_counter(4321)
    p, _ = make(store, count=count)
    await p.submit(URL)
    await p.run(KEY)
    assert (await store.get(KEY)).token_count == 4321
    assert count.seen == [await store.get_markdown(KEY)]


@pytest.mark.asyncio
async def test_over_token_cap_fails_without_storing(store):
    p, _ = make(store, count=make_counter(200_000), max_tokens=150_000)
    await p.submit(URL)
    await p.run(KEY)
    rec = await store.get(KEY)
    assert rec.status == "failed" and "too large" in rec.error
    assert rec.token_count is None and await store.get_markdown(KEY) is None


@pytest.mark.asyncio
async def test_exactly_at_cap_is_allowed(store):
    p, _ = make(store, count=make_counter(150_000), max_tokens=150_000)
    await p.submit(URL)
    await p.run(KEY)
    assert (await store.get(KEY)).status == "ready"


@pytest.mark.asyncio
async def test_char_guard_skips_counter_call(store):
    count = make_counter()
    p, _ = make(store, count=count, max_chars=10)
    await p.submit(URL)
    await p.run(KEY)
    assert (await store.get(KEY)).status == "failed"
    assert count.seen == []


@pytest.mark.asyncio
async def test_counter_failure_is_retryable(store):
    p, _ = make(store, count=make_counter(exc=LLMError("upstream detail")))
    await p.submit(URL)
    await p.run(KEY)
    rec = await store.get(KEY)
    assert rec.status == "failed" and "try again" in rec.error and "upstream" not in rec.error

    p2, _ = make(store)
    assert (await p2.submit(URL)).started is True
    await p2.run(KEY)
    assert (await store.get(KEY)).status == "ready"