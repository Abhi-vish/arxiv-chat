"""Streamlit UI for end-to-end arXiv ingestion and Gemini Q&A testing."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Iterator

import streamlit as st

from src.config import get_settings
from src.ingestion.arxiv import ArxivClient, FetchFailed, InvalidArxivUrl, PaperNotFound
from src.ingestion.pipeline import IngestionPipeline
from src.llm import LLMClient, LLMError
from src.qa.base import PaperDoc, Turn
from src.qa.full_context import FullContextStrategy
from src.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

INGEST_TIMEOUT_S = 300


# ingest

async def ingest(url: str, on_status: Callable[[str], None]) -> tuple[PaperDoc | None, str | None]:
    """Returns (paper, error). Fresh client per call: httpx clients are bound
    to one event loop and Streamlit gives us a new loop each time."""
    s = get_settings()
    store = Store(s.db_path)
    async with ArxivClient(user_agent=s.arxiv_user_agent) as client:
        pipeline = IngestionPipeline(client, store)
        sub = await pipeline.submit(url)
        key = sub.record.cache_key
        task = asyncio.create_task(pipeline.run(key)) if sub.started else None

        deadline = time.monotonic() + INGEST_TIMEOUT_S
        while True:
            rec = await store.get(key)
            on_status(rec.status)
            if rec.status in ("ready", "failed"):
                break
            if time.monotonic() > deadline:
                return None, "Timed out waiting for ingestion."
            await asyncio.sleep(0.5)
        if task:
            await task

        if rec.status == "failed":
            return None, rec.error
        md = await store.get_markdown(key)
        return PaperDoc(key, rec.title, rec.authors, rec.abstract, md or ""), None


# chat

def stream_answer(paper: PaperDoc, question: str, history: list[Turn]) -> Iterator[str]:
    """Bridge async generator -> sync generator for st.write_stream."""
    s = get_settings()
    loop = asyncio.new_event_loop()

    async def start():
        llm = LLMClient(
            api_key=s.gemini_api_key.get_secret_value(),
            model=s.llm_model,
            max_tokens=s.llm_max_tokens,
            timeout_s=s.llm_timeout_s,
            max_retries=s.llm_retries,
        )
        strategy = FullContextStrategy(llm, max_history_turns=s.max_history_turns)
        return strategy.answer([paper], question, history)

    agen = loop.run_until_complete(start())
    try:
        while True:
            try:
                yield loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                break
    finally:
        loop.run_until_complete(agen.aclose())
        loop.close()


# UI

st.set_page_config(page_title="arXiv Q&A (test)", layout="wide")
st.title("arXiv Q&A: pipeline test")

st.session_state.setdefault("paper", None)
st.session_state.setdefault("messages", [])

with st.sidebar:
    url = st.text_input("arXiv URL", placeholder="https://arxiv.org/abs/1706.03762")
    if st.button("Load paper", type="primary", disabled=not url.strip()):
        with st.status("Starting...", expanded=True) as status:
            try:
                paper, err = asyncio.run(
                    ingest(url, lambda st_: status.update(label=f"Status: {st_}"))
                )
            except InvalidArxivUrl as e:
                paper, err = None, f"Bad URL: {e}"
            except PaperNotFound:
                paper, err = None, "Paper not found on arXiv."
            except FetchFailed as e:
                paper, err = None, f"arXiv fetch failed: {e}"
            if err:
                status.update(label="Failed", state="error")
                st.error(err)
            else:
                status.update(label="Ready", state="complete")
                st.session_state.paper = paper
                st.session_state.messages = []

    paper: PaperDoc | None = st.session_state.paper
    if paper:
        st.success(paper.title)
        st.caption(f"{paper.cache_key} · {len(paper.markdown):,} chars")
        if st.button("Clear chat"):
            st.session_state.messages = []
            st.rerun()
        st.download_button("Download parsed markdown", paper.markdown,
                           file_name=f"{paper.cache_key.replace('/', '_')}.md")

paper = st.session_state.paper
if paper is None:
    st.info("Paste an arXiv link in the sidebar and click Load paper.")
    st.stop()

with st.expander("Parsed markdown preview (check parse quality here)"):
    st.code(paper.markdown[:6000], language="markdown")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

if question := st.chat_input("Ask about this paper"):
    history = [Turn(m["role"], m["content"]) for m in st.session_state.messages]
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            answer = st.write_stream(stream_answer(paper, question, history))
            st.session_state.messages.append({"role": "assistant", "content": answer})
        except LLMError as e:
            st.error(str(e))
            st.session_state.messages.pop()  # drop the unanswered question