from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from google import genai
from google.genai import types

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Upstream LLM call failed (after SDK retries)."""


def text_block(text: str, *, cache: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


class LLMClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str,
        max_tokens: int = 2048,
        timeout_s: float = 60.0,
        max_retries: int = 3,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens

        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_s * 1000),
            ),
        )

        self._async_client = self._client.aio

    @staticmethod
    def _extract_text(content: Any) -> str:

        if isinstance(content, str):
            return content

        if isinstance(content, dict) and content.get("type") == "text":
            return content["text"]

        if isinstance(content, list):
            return "\n".join(
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
            )

        return str(content)

    def _prepare_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:

        contents = []

        for message in messages:
            role = message["role"]

            if role == "assistant":
                role = "model"

            if role not in {"user", "model"}:
                raise ValueError(f"Unsupported message role: {role}")

            text = self._extract_text(message["content"])

            contents.append({
                "role": role,
                "parts": [{"text": text}],
            })

        return contents

    async def stream(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]:

        system_instruction = "\n\n".join(
            self._extract_text(block)
            for block in system
        )

        contents = self._prepare_messages(messages)

        usage = None
        finish_reason = None

        try:
            stream = await self._async_client.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction or None,
                    max_output_tokens=self._max_tokens,
                ),
            )

            async for chunk in stream:
                if chunk.text:
                    yield chunk.text

                if chunk.usage_metadata:
                    usage = chunk.usage_metadata

                if chunk.candidates:
                    finish_reason = chunk.candidates[0].finish_reason

        except Exception as e:
            log.exception("LLM call failed")
            raise LLMError("LLM call failed") from e

        self._log_usage(usage, finish_reason)

    async def count_tokens(self, text: str) -> int:
        try:
            response = await self._async_client.models.count_tokens(
                model=self._model,
                contents=text,
            )

        except Exception as e:
            log.exception("LLM count_tokens failed")
            raise LLMError("LLM count_tokens failed") from e

        return response.total_tokens

    def _log_usage(
        self,
        usage: Any,
        finish_reason: Any,
    ) -> None:

        if usage is None:
            log.warning("Gemini usage metadata unavailable")
            return

        log.info(
            "llm usage model=%s in=%s out=%s cached=%s finish=%s",
            self._model,
            usage.prompt_token_count,
            usage.candidates_token_count,
            usage.cached_content_token_count or 0,
            finish_reason,
        )

        if finish_reason == types.FinishReason.MAX_TOKENS:
            log.warning(
                "answer truncated at max_output_tokens=%s",
                self._max_tokens,
            )

    async def close(self) -> None:
        """Close the underlying Gemini client."""
        await self._client.aio.aclose()
