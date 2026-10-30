from __future__ import annotations

from typing import AsyncIterator, Sequence

from ..llm import LLMClient, text_block
from .base import PaperDoc, Turn
from .prompts import SYSTEM_PROMPT, build_paper_block

class FullContextStrategy:
    def __init__(self, llm:LLMClient, *, max_history_turns: int=10) -> None:
        self._llm = llm
        self._max_history_turns = max_history_turns
        
    async def answer(
        self, papers: Sequence[PaperDoc], question: str, history: Sequence[Turn]
    ) -> AsyncIterator[str]:
        if len(papers) != 1:
            raise ValueError("FullContextStrategy only supports a single paper")
        question = question.strip()
        if not question:
            raise ValueError("question is empty")
        
        system = [
            text_block(SYSTEM_PROMPT),
            text_block(build_paper_block(papers[0]), cache=True)
        ]
        messages = [{"role":t.role, "content":t.content} for t in self._trim(history)]
        messages.append({"role":"user", "content":question})
        
        async for chunk in self._llm.stream(system=system, messages=messages):
            yield chunk

    def _trim(self, history: Sequence[Turn]) -> list[Turn]:
        kept = list(history[-2 * self._max_history_turns :])
        while kept and kept[0].role != "user":
            kept.pop(0)
        return kept