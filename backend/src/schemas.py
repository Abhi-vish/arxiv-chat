from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .store import PaperRecord

MAX_QUESTION_CHARS=2000
MAX_HISTORY_TURNS=50
MAX_TURN_CHARS=20_000


class SubmitRequest(BaseModel):
    url: str = Field(min_length=1, max_length=300)


class PaperStatus(BaseModel):
    cache_key: str
    arxiv_id: str
    title: str
    authors: list[str]
    status: Literal["queued", "fetching", "parsing", "ready", "failed"]
    error: str | None = None
    source_kind: str | None = None
    token_count: int | None = None

    @classmethod
    def from_record(cls, r: PaperRecord) -> "PaperStatus":
        return cls(
            cache_key=r.cache_key,
            arxiv_id=r.arxiv_id.versioned,
            title=r.title,
            authors=r.authors,
            status=r.status,
            error=r.error,
            source_kind=r.source_kind,
            token_count=r.token_count,
        )


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_TURN_CHARS)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question is empty")
        return v