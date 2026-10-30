from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, TypeVar

from .ingestion.arxiv import ArxivId, PaperMetadata

status = Literal["queued", "fetching", "parsing", "ready", "failed"]
T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    cache_key TEXT PRIMARY KEY, --
    arxiv_id TEXT NOT NULL,
    version INTEGER,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    abstract TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    source_kind TEXT,
    markdown TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

_RECORD_COLS = (
    "cache_key, arxiv_id, version, title, authors, abstract, "
    "status, error, source_kind, updated_at"
)

@dataclass(frozen=True)
class PaperRecord:
    cache_key: str
    arxiv_id: ArxivId
    title: str
    authors: list[str]
    abstract: str
    status: status
    error: str | None
    source_kind: str | None
    updated_at: float
    
def _to_record(row: sqlite3.Row) -> PaperRecord:
    return PaperRecord(
        cache_key=row["cache_key"],
        arxiv_id=ArxivId(row["arxiv_id"], row["version"]),
        title=row["title"],
        authors=json.loads(row["authors"]),
        abstract=row["abstract"],
        status=row["status"],
        error=row["error"],
        source_kind=row["source_kind"],
        updated_at=row["updated_at"],
    )
    
class Store:
    def __init__(self, path: str | Path,*, clock: Callable[[], float] = time.time) -> None:
        self._path = str(path)
        self._clock = clock
        
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
            
    def _exec(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                return fn(conn)
        finally:
            conn.close()
            
    async def _run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._exec, fn)

    async def claim(self, meta: PaperMetadata, *, stale_after: float = 600.0) -> bool:
        """Atomically decide who runs ingestion. True => caller must run it.

        Claimable when: no row yet, row failed, or row stuck in an active
        state longer than stale_after (worker crashed). A 'ready' row is never
        claimed, so repeat submissions are cache hits.
        """
        now = self._clock()

        def op(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                "INSERT OR IGNORE INTO papers (cache_key, arxiv_id, version, title, "
                "authors, abstract, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                (
                    meta.id.cache_key, meta.id.base, meta.id.version, meta.title,
                    json.dumps(meta.authors), meta.abstract, now, now,
                ),
            )
            if cur.rowcount == 1:
                return True
            cur = conn.execute(
                "UPDATE papers SET status='queued', error=NULL, updated_at=? "
                "WHERE cache_key=? AND (status='failed' OR "
                "(status IN ('queued','fetching','parsing') AND updated_at < ?))",
                (now, meta.id.cache_key, now - stale_after),
            )
            return cur.rowcount == 1

        return await self._run(op)

    async def get(self, cache_key: str) -> PaperRecord | None:
        """Record without markdown (cheap; for status polling)."""
        def op(conn: sqlite3.Connection) -> PaperRecord | None:
            row = conn.execute(
                f"SELECT {_RECORD_COLS} FROM papers WHERE cache_key=?", (cache_key,)
            ).fetchone()
            return _to_record(row) if row else None

        return await self._run(op)

    async def get_markdown(self, cache_key: str) -> str | None:
        def op(conn: sqlite3.Connection) -> str | None:
            row = conn.execute(
                "SELECT markdown FROM papers WHERE cache_key=? AND status='ready'",
                (cache_key,),
            ).fetchone()
            return row["markdown"] if row else None

        return await self._run(op)

    async def set_status(self, cache_key: str, status: status) -> None:
        """Also acts as a heartbeat (bumps updated_at)."""
        now = self._clock()
        await self._run(lambda c: c.execute(
            "UPDATE papers SET status=?, updated_at=? WHERE cache_key=?",
            (status, now, cache_key),
        ))

    async def mark_ready(self, cache_key: str, markdown: str, source_kind: str) -> None:
        now = self._clock()
        await self._run(lambda c: c.execute(
            "UPDATE papers SET status='ready', error=NULL, markdown=?, source_kind=?, "
            "updated_at=? WHERE cache_key=?",
            (markdown, source_kind, now, cache_key),
        ))

    async def mark_failed(self, cache_key: str, error: str) -> None:
        now = self._clock()
        await self._run(lambda c: c.execute(
            "UPDATE papers SET status='failed', error=?, updated_at=? WHERE cache_key=?",
            (error, now, cache_key),
        ))