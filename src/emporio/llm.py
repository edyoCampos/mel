"""Provider-independent chat model interface (D-09, NFR-05, NFR-06).

The agent depends only on the LLMClient protocol. The OpenAI-compatible adapter talks
to Google Gemini (default) or Groq (alternative); switching is only environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]

ENV_VARIABLES = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None  # for role == "tool"


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] = field(default_factory=dict)


class LLMError(Exception):
    """Timeout, network or auth error, rate limit after retries, or malformed response."""


class LLMClient(Protocol):
    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0  # deterministic answers
    timeout_seconds: float = 30.0
    max_attempts: int = 3  # D-09: at most 3 attempts on HTTP 429
    backoff_base_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> LLMSettings:
        """Read os.environ only; the CLI loads .env before calling this."""
        missing = [name for name in ENV_VARIABLES if not os.environ.get(name, "").strip()]
        if missing:
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")
        return cls(
            base_url=os.environ["LLM_BASE_URL"].strip(),
            api_key=os.environ["LLM_API_KEY"].strip(),
            model=os.environ["LLM_MODEL"].strip(),
        )
