import pytest

from backend.src.ingestion.arxiv import ArxivId, PaperMetadata
from backend.src.store import Store

META = PaperMetadata(ArxivId("hep-th/9901001", 2), "Title", ["A", "B"], "Abstract")
KEY = META.id.cache_key  # "hep-th_9901001v2"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


@pytest.mark.asyncio
async def test_claim_creates_queued_row_and_roundtrips_fields(store):
    assert await store.claim(META) is True
    rec = await store.get(KEY)
    assert rec.status == "queued"
    assert rec.arxiv_id == META.id
    assert (rec.title, rec.authors, rec.abstract) == ("Title", ["A", "B"], "Abstract")


@pytest.mark.asyncio
async def test_second_claim_loses_while_active(store):
    assert await store.claim(META) is True
    assert await store.claim(META) is False


@pytest.mark.asyncio
async def test_failed_is_reclaimable_and_clears_error(store):
    await store.claim(META)
    await store.mark_failed(KEY, "boom")
    assert (await store.get(KEY)).error == "boom"
    assert await store.claim(META) is True
    rec = await store.get(KEY)
    assert rec.status == "queued" and rec.error is None


@pytest.mark.asyncio
async def test_stale_active_row_is_reclaimable(tmp_path):
    t = [1000.0]
    store = Store(tmp_path / "t.db", clock=lambda: t[0])
    await store.claim(META)
    await store.set_status(KEY, "fetching")
    t[0] += 599
    assert await store.claim(META, stale_after=600) is False
    t[0] += 2
    assert await store.claim(META, stale_after=600) is True


@pytest.mark.asyncio
async def test_ready_never_reclaimed_even_if_old(tmp_path):
    t = [0.0]
    store = Store(tmp_path / "t.db", clock=lambda: t[0])
    await store.claim(META)
    await store.mark_ready(KEY, "# md", "html")
    t[0] += 10**9
    assert await store.claim(META, stale_after=1) is False


@pytest.mark.asyncio
async def test_markdown_only_when_ready(store):
    await store.claim(META)
    assert await store.get_markdown(KEY) is None
    await store.mark_ready(KEY, "# hello", "pdf")
    assert await store.get_markdown(KEY) == "# hello"
    assert (await store.get(KEY)).source_kind == "pdf"


@pytest.mark.asyncio
async def test_get_missing(store):
    assert await store.get("nope") is None