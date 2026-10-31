from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..llm import LLMError
from ..store import PaperRecord, Store
from .arxiv import (
    ArxivClient,
    FetchedContent,
    FetchFailed,
    PaperNotFound,
    PaperTooLarge,
    parse_arxiv_url,
)
from .parser import ParsedPaper, ParseFailed, parse_content

log = logging.getLogger(__name__)

# Cheap sanity cap so we never send a giant string to the counting API.
# The real limit is max_tokens.
MAX_CHARS = 1_500_000

CountTokens = Callable[[str], Awaitable[int]]


@dataclass(frozen=True)
class Submission:
    record: PaperRecord
    started: bool  # True => caller must schedule pipeline.run(record.cache_key)


class IngestionPipeline:
    def __init__(
        self,
        client: ArxivClient,
        store: Store,
        *,
        count_tokens: CountTokens,   # required: a missing size guard must be a loud error
        max_tokens: int = 150_000,
        parse: Callable[[FetchedContent], ParsedPaper] = parse_content,
        max_chars: int = MAX_CHARS,
        stale_after: float = 600.0,
    ) -> None:
        self._client = client
        self._store = store
        self._count_tokens = count_tokens
        self._max_tokens = max_tokens
        self._parse = parse
        self._max_chars = max_chars
        self._stale_after = stale_after

    async def submit(self, raw_url: str) -> Submission:
        """Fast path for the HTTP request. Does one arXiv API call."""
        arxiv_id = parse_arxiv_url(raw_url)
        meta = await self._client.fetch_metadata(arxiv_id)
        started = await self._store.claim(meta, stale_after=self._stale_after)
        record = await self._store.get(meta.id.cache_key)
        if record is None:  
            raise RuntimeError(f"row vanished after claim: {meta.id.cache_key}")
        return Submission(record, started)

    async def run(self, cache_key: str) -> None:
        """Slow path (background task). Never raises; failures land in the DB."""
        record = await self._store.get(cache_key)
        if record is None:
            log.error("run() for unknown cache_key=%s", cache_key)
            return
        try:
            await self._store.set_status(cache_key, "fetching")
            content = await self._client.fetch_content(record.arxiv_id)

            await self._store.set_status(cache_key, "parsing")
            parsed = await asyncio.to_thread(self._parse, content)
            md = parsed.markdown

            if len(md) > self._max_chars:
                raise PaperTooLarge(f"{len(md)} chars > cap {self._max_chars}")
            tokens = await self._count_tokens(md)
            if tokens > self._max_tokens:
                raise PaperTooLarge(f"{tokens} tokens > cap {self._max_tokens}")

            await self._store.mark_ready(cache_key, md, parsed.kind, tokens)
            log.info("ingested %s via %s (%d chars, %d tokens)",
                     cache_key, parsed.kind, len(md), tokens)
        except (PaperNotFound, PaperTooLarge, FetchFailed, ParseFailed, LLMError) as e:
            log.warning("ingestion failed for %s: %s: %s", cache_key, type(e).__name__, e)
            await self._store.mark_failed(cache_key, _user_message(e))
        except Exception:
            log.exception("unexpected ingestion error for %s", cache_key)
            await self._store.mark_failed(cache_key, "Internal error while processing the paper.")


def _user_message(e: Exception) -> str:
    if isinstance(e, PaperNotFound):
        return "Paper not found on arXiv."
    if isinstance(e, PaperTooLarge):
        return "This paper is too large to process."
    if isinstance(e, FetchFailed):
        return "Could not download the paper from arXiv. Please try again later."
    if isinstance(e, LLMError):
        return "Could not check the paper's size right now. Please try again."
    return "Could not extract readable text from this paper."