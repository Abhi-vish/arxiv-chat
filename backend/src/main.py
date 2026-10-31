from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings, get_settings
from .ingestion.arxiv import (
    ArxivClient,
    FetchFailed,
    InvalidArxivUrl,
    PaperNotFound,
)
from .ingestion.pipeline import IngestionPipeline
from .llm import LLMClient, LLMError
from .qa.base import AnswerStrategy, PaperDoc, Turn
from .qa.full_context import FullContextStrategy
from .schemas import ChatRequest, PaperStatus, SubmitRequest
from .store import Store

log = logging.getLogger(__name__)


# dependencies

@dataclass
class Container:
    store: Store
    pipeline: IngestionPipeline
    strategy: AnswerStrategy
    close: Callable[[], Awaitable[None]] | None = None


def build_container(s: Settings) -> Container:
    store = Store(s.db_path)
    client = ArxivClient(user_agent=s.arxiv_user_agent)  # one client => one rate limiter
    pipeline = IngestionPipeline(client, store)
    llm = LLMClient(
        api_key=s.gemini_api_key.get_secret_value(),
        model=s.llm_model,
        max_tokens=s.llm_max_tokens,
        timeout_s=s.llm_timeout_s,
        max_retries=s.llm_retries,
    )
    strategy = FullContextStrategy(llm, max_history_turns=s.max_history_turns)
    return Container(store, pipeline, strategy, close=client.aclose)


def get_container(request: Request) -> Container:
    return request.app.state.container


class RequestLimiter:
    """In-memory sliding window per key. Process-local: use Redis when scaling out."""

    def __init__(self, limit: int, window: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._limit, self._window, self._clock = limit, window, clock
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> float | None:
        """None if allowed, else seconds until the next slot opens."""
        now = self._clock()
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - self._window:
            q.popleft()
        if len(q) >= self._limit:
            return q[0] + self._window - now
        q.append(now)
        if len(self._hits) > 10_000:  # bound memory
            self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - self._window}
        return None


def limited(limiter: RequestLimiter):
    async def dep(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        retry = limiter.check(ip)
        if retry is not None:
            raise HTTPException(
                429, "Too many requests. Please slow down.",
                headers={"Retry-After": str(max(1, math.ceil(retry)))},
            )
    return dep


# SSE

def _event(name: str, data: dict) -> str:
    # JSON-encode so newlines inside text cannot break SSE framing.
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


async def _sse(gen: AsyncIterator[str]) -> AsyncIterator[str]:
    """Headers are already sent, so failures become an `error` event."""
    try:
        async for chunk in gen:
            yield _event("token", {"text": chunk})
        yield _event("done", {})
    except LLMError as e:
        yield _event("error", {"message": str(e)})
    except Exception:
        log.exception("chat stream failed")
        yield _event("error", {"message": "Something went wrong. Please try again."})
    finally:
        aclose = getattr(gen, "aclose", None)
        if aclose:
            await aclose()


# app

def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    ingest_limiter = RequestLimiter(s.ingest_rate_per_min)
    chat_limiter = RequestLimiter(s.chat_rate_per_min)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = None
        if getattr(app.state, "container", None) is None:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(name)s %(levelname)s %(message)s",
            )
            owned = build_container(s)
            app.state.container = owned
        try:
            yield
        finally:
            if owned and owned.close:
                await owned.close()

    app = FastAPI(title="arXiv Q&A", lifespan=lifespan)
    app.state.container = container  # tests inject fakes; None => lifespan builds
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    # Domain errors -> HTTP. Upstream detail stays in logs, never in responses.
    @app.exception_handler(InvalidArxivUrl)
    async def _bad_url(_: Request, e: InvalidArxivUrl):
        return JSONResponse({"detail": f"Invalid arXiv link: {e}"}, status_code=400)

    @app.exception_handler(PaperNotFound)
    async def _not_found(_: Request, e: PaperNotFound):
        return JSONResponse({"detail": "Paper not found on arXiv."}, status_code=404)

    @app.exception_handler(FetchFailed)
    async def _fetch_failed(_: Request, e: FetchFailed):
        log.warning("arXiv fetch failed: %s", e)
        return JSONResponse({"detail": "Could not reach arXiv. Please try again later."},
                            status_code=502)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/papers", response_model=PaperStatus,
              responses={200: {"description": "Already processed (cache hit)"},
                         202: {"description": "Processing started or in progress"}})
    async def submit_paper(
        body: SubmitRequest,
        background: BackgroundTasks,
        response: Response,
        c: Container = Depends(get_container),
        _: None = Depends(limited(ingest_limiter)),
    ) -> PaperStatus:
        sub = await c.pipeline.submit(body.url)
        if sub.started:
            background.add_task(c.pipeline.run, sub.record.cache_key)
        response.status_code = 200 if sub.record.status == "ready" else 202
        return PaperStatus.from_record(sub.record)

    @app.get("/papers/{cache_key}", response_model=PaperStatus)
    async def paper_status(cache_key: str, c: Container = Depends(get_container)) -> PaperStatus:
        rec = await c.store.get(cache_key)
        if rec is None:
            raise HTTPException(404, "Paper not found. Submit it first.")
        return PaperStatus.from_record(rec)

    @app.post("/papers/{cache_key}/chat")
    async def chat(
        cache_key: str,
        body: ChatRequest,
        c: Container = Depends(get_container),
        _: None = Depends(limited(chat_limiter)),
    ) -> StreamingResponse:
        # All checks happen BEFORE streaming so errors get real status codes.
        rec = await c.store.get(cache_key)
        if rec is None:
            raise HTTPException(404, "Paper not found. Submit it first.")
        if rec.status == "failed":
            raise HTTPException(409, rec.error or "Processing this paper failed.")
        if rec.status != "ready":
            raise HTTPException(409, f"Paper is still processing ({rec.status}).")
        markdown = await c.store.get_markdown(cache_key)
        if markdown is None:
            raise HTTPException(409, "Paper content is unavailable.")

        paper = PaperDoc(rec.cache_key, rec.title, rec.authors, rec.abstract, markdown)
        history = [Turn(t.role, t.content) for t in body.history]
        stream = c.strategy.answer([paper], body.question, history)
        return StreamingResponse(
            _sse(stream),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app