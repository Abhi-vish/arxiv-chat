import httpx
import pytest
import respx

from backend.src.ingestion.arxiv import (
    API_URL,
    ArxivClient,
    FetchFailed,
    PaperNotFound,
    PaperTooLarge,
    RateLimiter,
    parse_arxiv_url,
)

ID = parse_arxiv_url("2401.12345")

ATOM_OK = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <title>Attention
      Is Still All You Need</title>
    <summary>  We study   attention.
    </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
  </entry>
</feed>"""

ATOM_EMPTY = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/Atom"></feed>""".replace(
    "http://www.w3.org/Atom", "http://www.w3.org/2005/Atom"
)

ATOM_ERROR = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/api/errors#incorrect_id_format_for_1</id>
<title>Error</title><summary>bad id</summary></entry></feed>"""


async def no_sleep(_: float) -> None:
    return None


def make_client(**kw) -> ArxivClient:
    return ArxivClient(min_interval=0, sleep=no_sleep, **kw)


# ---- metadata ----

@pytest.mark.asyncio
@respx.mock
async def test_metadata_ok_resolves_version_and_normalizes_whitespace():
    respx.get(API_URL, params={"id_list": "2401.12345"}).respond(200, text=ATOM_OK)
    async with make_client() as c:
        meta = await c.fetch_metadata(ID)
    assert meta.id.versioned == "2401.12345v2"
    assert meta.title == "Attention Is Still All You Need"
    assert meta.abstract == "We study attention."
    assert meta.authors == ["Ada Lovelace", "Alan Turing"]


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("body", [ATOM_EMPTY, ATOM_ERROR])
async def test_metadata_not_found(body):
    respx.get(API_URL).respond(200, text=body)
    async with make_client() as c:
        with pytest.raises(PaperNotFound):
            await c.fetch_metadata(ID)


@pytest.mark.asyncio
@respx.mock
async def test_metadata_garbage_xml():
    respx.get(API_URL).respond(200, text="<not xml")
    async with make_client() as c:
        with pytest.raises(FetchFailed):
            await c.fetch_metadata(ID)


# ---- content ----

@pytest.mark.asyncio
@respx.mock
async def test_content_prefers_html():
    respx.get(ID.html_url).respond(
        200, content=b"<html>hi</html>", headers={"content-type": "text/html; charset=utf-8"}
    )
    pdf = respx.get(ID.pdf_url).respond(200, content=b"%PDF")
    async with make_client() as c:
        got = await c.fetch_content(ID)
    assert got.kind == "html" and got.data == b"<html>hi</html>"
    assert not pdf.called


@pytest.mark.asyncio
@respx.mock
async def test_content_falls_back_to_pdf_on_html_404():
    respx.get(ID.html_url).respond(404)
    respx.get(ID.pdf_url).respond(200, content=b"%PDF-1.5 data")
    async with make_client() as c:
        got = await c.fetch_content(ID)
    assert got.kind == "pdf" and got.data.startswith(b"%PDF")


@pytest.mark.asyncio
@respx.mock
async def test_content_falls_back_to_pdf_when_html_keeps_failing():
    respx.get(ID.html_url).respond(503)
    respx.get(ID.pdf_url).respond(200, content=b"%PDF")
    async with make_client() as c:
        got = await c.fetch_content(ID)
    assert got.kind == "pdf"


@pytest.mark.asyncio
@respx.mock
async def test_content_not_found_when_both_404():
    respx.get(ID.html_url).respond(404)
    respx.get(ID.pdf_url).respond(404)
    async with make_client() as c:
        with pytest.raises(PaperNotFound):
            await c.fetch_content(ID)


@pytest.mark.asyncio
@respx.mock
async def test_pdf_too_large():
    respx.get(ID.html_url).respond(404)
    respx.get(ID.pdf_url).respond(200, content=b"x" * 100)
    async with make_client(max_pdf_bytes=10) as c:
        with pytest.raises(PaperTooLarge):
            await c.fetch_content(ID)


# ---- retry ----

@pytest.mark.asyncio
@respx.mock
async def test_retries_then_succeeds():
    route = respx.get(API_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(429), httpx.Response(200, text=ATOM_OK)]
    )
    async with make_client() as c:
        meta = await c.fetch_metadata(ID)
    assert route.call_count == 3 and meta.title


@pytest.mark.asyncio
@respx.mock
async def test_retries_exhausted_raises():
    route = respx.get(API_URL).respond(503)
    async with make_client(max_attempts=2) as c:
        with pytest.raises(FetchFailed):
            await c.fetch_metadata(ID)
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_transport_error_retried():
    route = respx.get(API_URL).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, text=ATOM_OK)]
    )
    async with make_client() as c:
        await c.fetch_metadata(ID)
    assert route.call_count == 2


# ---- rate limiter ----

@pytest.mark.asyncio
async def test_rate_limiter_spaces_calls():
    now = [0.0]
    slept: list[float] = []

    async def fake_sleep(d: float) -> None:
        slept.append(d)
        now[0] += d

    rl = RateLimiter(3.0, clock=lambda: now[0], sleep=fake_sleep)
    await rl.wait()          # first call: no wait
    now[0] += 1.0
    await rl.wait()          # 1s elapsed -> sleep 2s
    assert slept == [2.0]