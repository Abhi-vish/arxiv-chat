from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

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

MAX_CHARS = 600_000

@dataclass(frozen=True)
class Submission:
    record: PaperRecord
    started: bool

class IngestionPipeline:
    def __init__(
        self,
        client: ArxivClient,
        store: Store,
        *,
        parse: Callable[[FetchedContent], ParsedPaper] = parse_content,
        max_chars: int = MAX_CHARS,
        stale_after: float = 600.0
    ) -> None:
        self._client = client
        self._store = store
        self._parse = parse
        self._max_chars = max_chars
        self._stale_after = stale_after
        
    async def submit(self, raw_url: str) -> Submission:
        arxiv_id = parse_arxiv_url(raw_url)
        meta = await self._client.fetch_metadata(arxiv_id)
        started = await self._store.claim(meta, stale_after=self._stale_after)
        record = await self._store.get(meta.id.cache_key)
        if record is None:  # cannot happen unless the DB is broken
            raise RuntimeError(f"row vanished after claim: {meta.id.cache_key}")
        return Submission(record, started)
    
    async def run(self, cache_key: str) -> None:
        record = await self._store.get(cache_key)
        if record is None:
            log.error("run() called with unknown cache_key %s", cache_key)
            return
        try:
            await self._store.set_status(cache_key, "fetching")
            content = await self._client.fetch_content(record.arxiv_id)
            
            await self._store.set_status(cache_key, "parsing")
            parsed = await asyncio.to_thread(self._parse, content)
            
            if len(parsed.markdown) > self._max_chars:
                raise PaperTooLarge(
                    f"Paper {record.arxiv_id} exceeds max_chars={self._max_chars}"
                )
                
            await self._store.mark_ready(cache_key, parsed.markdown, parsed.kind)
            log.info("Ingested %s via %s (%d chars)",cache_key, parsed.kind, len(parsed.markdown))
            
        except (FetchFailed, PaperNotFound, PaperTooLarge, ParseFailed) as e:
            log.exception("Failed to ingest %s: %s", cache_key, e)
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
    return "Could not extract readable text from this paper."