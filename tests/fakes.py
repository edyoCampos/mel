"""Test doubles: a scripted LLM that never touches the network (NFR-05)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from emporio.llm import LLMResponse, Message, ToolCall


class FakeLLM:
    """Plays a scripted list of responses (or exceptions) and records every request."""

    def __init__(self, script: Iterable[LLMResponse | Exception]) -> None:
        self._script = list(script)
        self.requests: list[tuple[list[Message], list[dict[str, Any]] | None]] = []

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        self.requests.append((list(messages), tools))
        if not self._script:
            raise AssertionError("FakeLLM script exhausted")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def text(content: str) -> LLMResponse:
    return LLMResponse(content=content)


def call(name: str, arguments: str = "{}", call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def tool_calls(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=tuple(calls))
