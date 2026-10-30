from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol, Sequence

@dataclass(frozen=True)
class PaperDoc:
    cache_key: str
    title: str
    authors: list[str]
    abstract: str
    markdown: str

@dataclass(frozen=True)
class Turn:
    role: Literal["user", "assistant"]
    content: str
    
class AnswerStrategy(Protocol):
    
    def answer(
        self, papers: Sequence[PaperDoc], question: str, history: Sequence[Turn]
    ) -> AsyncIterator[str]:
        ...