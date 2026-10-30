import logging
from types import SimpleNamespace

import pytest

from backend.src.llm import LLMClient, LLMError, text_block


class FakeStream:
    def __init__(self, chunks, *, exc=None, finish_reason="STOP"):
        self._chunks, self._exc, self._finish_reason = chunks, exc, finish_reason

    def __aiter__(self):
        return self._generate()

    async def _generate(self):
        for c in self._chunks:
            yield SimpleNamespace(
                text=c,
                usage_metadata=SimpleNamespace(
                    prompt_token_count=10,
                    candidates_token_count=5,
                    cached_content_token_count=100,
                ),
                candidates=[
                    SimpleNamespace(finish_reason=self._finish_reason)
                ],
            )
        if self._exc:
            raise self._exc


class FakeModels:
    def __init__(self, stream, count=123):
        self._stream, self.stream_kwargs, self.count_kwargs = stream, None, None
        self._count = count

    async def generate_content_stream(self, **kwargs):
        self.stream_kwargs = kwargs
        return self._stream

    async def count_tokens(self, **kwargs):
        self.count_kwargs = kwargs
        return SimpleNamespace(total_tokens=self._count)


def make(stream):
    models = FakeModels(stream)
    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=models,
            aclose=lambda: None,
        )
    )
    llm = LLMClient(model="m", max_tokens=99, client=client)
    return llm, models


def test_text_block_cache_flag():
    assert "cache_control" not in text_block("a")
    assert text_block("a", cache=True)["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_stream_yields_and_passes_params(caplog):
    llm, models = make(FakeStream(["a", "b"]))
    sys_, messages = [text_block("s")], [{"role": "user", "content": "q"}]
    with caplog.at_level(logging.INFO):
        out = [c async for c in llm.stream(system=sys_, messages=messages)]
    assert out == ["a", "b"]
    assert models.stream_kwargs["model"] == "m"
    assert models.stream_kwargs["contents"] == [
        {"role": "user", "parts": [{"text": "q"}]}
    ]
    config = models.stream_kwargs["config"]
    assert config.system_instruction == "s"
    assert config.max_output_tokens == 99
    assert "cached=100" in caplog.text


@pytest.mark.asyncio
async def test_truncation_warned(caplog):
    from google.genai import types

    llm, _ = make(FakeStream(["a"], finish_reason=types.FinishReason.MAX_TOKENS))
    with caplog.at_level(logging.WARNING):
        [c async for c in llm.stream(system=[], messages=[])]
    assert "truncated" in caplog.text


@pytest.mark.asyncio
async def test_api_error_mapped_and_detail_hidden():
    llm, _ = make(FakeStream(["a"], exc=RuntimeError("secret detail")))
    got = []
    with pytest.raises(LLMError) as ei:
        async for c in llm.stream(system=[], messages=[]):
            got.append(c)
    assert got == ["a"]
    assert str(ei.value) == "LLM call failed"


@pytest.mark.asyncio
async def test_count_tokens():
    llm, models = make(FakeStream([]))
    assert await llm.count_tokens("hello") == 123
    assert models.count_kwargs == {"model": "m", "contents": "hello"}