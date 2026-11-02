"""Provider-independent chat model interface (D-09, NFR-05, NFR-06).

The agent depends only on the LLMClient protocol. The OpenAI-compatible adapter talks
to Groq (default) or Google Gemini (alternative); switching is only environment variables.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import openai

Role = Literal["system", "user", "assistant", "tool"]

ENV_VARIABLES = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model
    # Opaque provider data echoed back on the next request, e.g. Gemini's
    # thought_signature; Gemini 3 answers HTTP 400 without it (AS-72).
    extra: dict[str, Any] | None = None


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
    # Optional LLM_REASONING_EFFORT ("low", "none", ...); empty means not sent (AS-76)
    reasoning_effort: str | None = None

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
            reasoning_effort=os.environ.get("LLM_REASONING_EFFORT", "").strip() or None,
        )


# HTTP 429 (rate limit), 502 and 503 (provider overloaded) share the retry policy (AS-81)
RETRYABLE_STATUS = frozenset({429, 502, 503})
DEGENERATE_ALNUM_RATIO = 0.3


def is_degenerate(content: str | None) -> bool:
    """Empty text, or text that is mostly symbols, zero-width spaces and ellipses (AS-81)."""
    visible = [c for c in (content or "") if not c.isspace()]
    if not visible:
        return True
    return sum(c.isalnum() for c in visible) / len(visible) < DEGENERATE_ALNUM_RATIO


RETRY_AFTER_CAP_SECONDS = 60.0  # AS-37, AS-73: per-minute token quotas reset within 60 s


def _to_openai(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        data["tool_calls"] = [_tool_call_to_openai(call) for call in message.tool_calls]
    return data


def _tool_call_to_openai(call: ToolCall) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments},
    }
    if call.extra:
        data["extra_content"] = call.extra
    return data


def _retry_after(error: openai.APIStatusError) -> float | None:
    value = error.response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class OpenAICompatibleClient:
    """LLMClient for OpenAI-compatible endpoints (Groq by default, Gemini as alternative)."""

    def __init__(self, settings: LLMSettings, sleep: Callable[[float], None] = time.sleep) -> None:
        self._settings = settings
        self._sleep = sleep
        # max_retries=0: the only retry logic is ours, on HTTP 429 (AS-36)
        self._client = openai.OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
            max_retries=0,
        )

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        request: dict[str, Any] = {
            "model": self._settings.model,
            "messages": [_to_openai(m) for m in messages],
            "temperature": self._settings.temperature,
        }
        if tools:
            request["tools"] = tools
        if self._settings.reasoning_effort:
            request["reasoning_effort"] = self._settings.reasoning_effort
        attempts = self._settings.max_attempts
        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            wait = self._settings.backoff_base_seconds * 2 ** (attempt - 1)
            try:
                response = self._parse(self._client.chat.completions.create(**request))
            except openai.APIStatusError as exc:
                status = exc.status_code
                if status not in RETRYABLE_STATUS:
                    raise LLMError(f"provider returned HTTP {status}") from None
                if last:
                    if status == 429:
                        raise LLMError(
                            f"rate limited by provider after {attempts} attempts"
                        ) from None
                    raise LLMError(
                        f"provider returned HTTP {status} after {attempts} attempts"
                    ) from None
                hinted = _retry_after(exc)
                if hinted is not None and hinted > RETRY_AFTER_CAP_SECONDS:
                    # e.g. a daily token quota; waiting the cap cannot help (AS-75)
                    raise LLMError(f"rate limited by provider; retry in {hinted:.0f} s") from None
                if hinted is not None:
                    wait = min(max(wait, hinted), RETRY_AFTER_CAP_SECONDS)
                self._sleep(wait)
                continue
            except openai.APITimeoutError:
                raise LLMError("provider timed out") from None
            except openai.APIConnectionError:
                raise LLMError("could not connect to the provider") from None
            except openai.OpenAIError as exc:
                raise LLMError(f"provider error: {type(exc).__name__}") from None
            if response.tool_calls or not is_degenerate(response.content):
                return response
            # Empty or garbage text is never shown to the customer (AS-81)
            if last:
                raise LLMError("empty or degenerate response from provider")
            self._sleep(wait)
        raise LLMError("no response from provider")  # pragma: no cover

    @staticmethod
    def _parse(response: Any) -> LLMResponse:
        try:
            message = response.choices[0].message
        except (AttributeError, IndexError) as exc:
            raise LLMError("malformed response from provider") from exc
        calls = tuple(
            ToolCall(
                id=call.id or f"call_{index}",
                name=call.function.name,
                arguments=call.function.arguments or "{}",
                extra=getattr(call, "extra_content", None) or None,
            )
            for index, call in enumerate(message.tool_calls or [])
        )
        usage: dict[str, int] = {}
        if getattr(response, "usage", None) is not None:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(response.usage, key, None)
                if isinstance(value, int):
                    usage[key] = value
        return LLMResponse(content=message.content, tool_calls=calls, usage=usage)
