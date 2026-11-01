"""OpenAICompatibleClient: message conversion, error wrapping and 429 retry (D-09)."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx  # transport used by the openai SDK (transitive dependency)
import openai
import pytest

from emporio.llm import LLMError, LLMSettings, Message, OpenAICompatibleClient, ToolCall

KEY = "secret-test-key-123"
SETTINGS = LLMSettings(base_url="https://example.test/v1/", api_key=KEY, model="test-model")
REQUEST = httpx.Request("POST", "https://example.test/v1/chat/completions")


def http_error(cls: type[openai.APIStatusError], status: int, **headers: str) -> Exception:
    response = httpx.Response(status, request=REQUEST, headers=headers)
    return cls(f"HTTP {status}", response=response, body=None)


def completion(content: str | None = "ok", calls: list[tuple[str, str, str]] | None = None) -> Any:
    tool_calls = [
        SimpleNamespace(id=i, type="function", function=SimpleNamespace(name=n, arguments=a))
        for i, n, a in calls or []
    ]
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class StubCompletions:
    def __init__(self, steps: list[Any]) -> None:
        self.steps = steps
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make_client(steps: list[Any]) -> tuple[OpenAICompatibleClient, StubCompletions, list[float]]:
    sleeps: list[float] = []
    client = OpenAICompatibleClient(SETTINGS, sleep=sleeps.append)
    stub = StubCompletions(steps)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=stub))  # type: ignore[assignment]
    return client, stub, sleeps


def test_message_conversion_to_openai_format() -> None:
    client, stub, _ = make_client([completion(calls=[("c1", "get_product_details", "{}")])])
    history = [
        Message(role="system", content="sys"),
        Message(role="user", content="oi"),
        Message(role="assistant", content=None, tool_calls=(ToolCall("c0", "ping", "{}"),)),
        Message(role="tool", content='{"pong": true}', tool_call_id="c0"),
    ]
    response = client.complete(history, tools=[{"type": "function"}])
    sent = stub.calls[0]
    assert sent["model"] == "test-model"
    assert sent["temperature"] == 0.0
    assert sent["tools"] == [{"type": "function"}]
    assert sent["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "oi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c0", "type": "function", "function": {"name": "ping", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c0", "content": '{"pong": true}'},
    ]
    assert response.tool_calls == (ToolCall("c1", "get_product_details", "{}"),)
    assert response.usage["total_tokens"] == 15


def test_tool_call_extra_content_round_trips() -> None:
    """Gemini 3 returns a thought_signature in extra_content and rejects (HTTP 400) a
    follow-up request whose assistant tool_calls lack it (AS-72)."""
    extra = {"google": {"thought_signature": "sig-abc"}}
    raw = completion(calls=[("c1", "get_store_hours", "{}")])
    raw.choices[0].message.tool_calls[0].extra_content = extra
    client, stub, _ = make_client([raw, completion("aberta")])
    first = client.complete([Message(role="user", content="oi")])
    assert first.tool_calls[0].extra == extra
    history = [
        Message(role="user", content="oi"),
        Message(role="assistant", content=None, tool_calls=first.tool_calls),
        Message(role="tool", content="ok", tool_call_id="c1"),
    ]
    client.complete(history)
    sent_call = stub.calls[1]["messages"][1]["tool_calls"][0]
    assert sent_call["extra_content"] == extra


def test_no_tools_argument_when_none() -> None:
    client, stub, _ = make_client([completion()])
    client.complete([Message(role="user", content="oi")])
    assert "tools" not in stub.calls[0]


def test_adapter_wraps_transport_errors() -> None:
    client, stub, sleeps = make_client([openai.APITimeoutError(request=REQUEST)])
    with pytest.raises(LLMError) as info:
        client.complete([Message(role="user", content="oi")])
    assert KEY not in str(info.value)
    assert len(stub.calls) == 1 and sleeps == []


def test_error_message_never_contains_api_key() -> None:
    client, _, _ = make_client([http_error(openai.AuthenticationError, 401)])
    with pytest.raises(LLMError) as info:
        client.complete([Message(role="user", content="oi")])
    assert KEY not in str(info.value)
    assert "401" in str(info.value) or "auth" in str(info.value).lower()


def test_retries_on_429_then_succeeds() -> None:
    limit = http_error(openai.RateLimitError, 429)
    client, stub, sleeps = make_client([limit, limit, completion("finally")])
    assert client.complete([Message(role="user", content="oi")]).content == "finally"
    assert len(stub.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_gives_up_after_three_attempts() -> None:
    limit = http_error(openai.RateLimitError, 429)
    client, stub, sleeps = make_client([limit, limit, limit, completion()])
    with pytest.raises(LLMError, match="3 attempts"):
        client.complete([Message(role="user", content="oi")])
    assert len(stub.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_does_not_retry_other_errors() -> None:
    client, stub, sleeps = make_client([http_error(openai.InternalServerError, 500)])
    with pytest.raises(LLMError):
        client.complete([Message(role="user", content="oi")])
    assert len(stub.calls) == 1 and sleeps == []


def test_respects_retry_after_header() -> None:
    limited = http_error(openai.RateLimitError, 429, **{"retry-after": "5"})
    client, _, sleeps = make_client([limited, completion()])
    client.complete([Message(role="user", content="oi")])
    assert sleeps == [5.0]


def test_retry_after_is_capped() -> None:
    # 60 s: per-minute token quotas (Groq free tier: 8000 TPM) reset within a minute (AS-73)
    limited = http_error(openai.RateLimitError, 429, **{"retry-after": "120"})
    client, _, sleeps = make_client([limited, completion()])
    client.complete([Message(role="user", content="oi")])
    assert sleeps == [60.0]


def test_retry_after_below_cap_is_honored() -> None:
    limited = http_error(openai.RateLimitError, 429, **{"retry-after": "23.4"})
    client, _, sleeps = make_client([limited, completion()])
    client.complete([Message(role="user", content="oi")])
    assert sleeps == [23.4]


def test_sdk_internal_retries_disabled() -> None:
    client = OpenAICompatibleClient(SETTINGS)
    assert client._client.max_retries == 0  # type: ignore[union-attr]


def test_env_example_lists_both_providers() -> None:
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    active = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert [line.split("=")[0] for line in active] == ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"]
    # D-09 fallback (AS-72): Groq is the active default, Gemini the commented alternative
    assert "LLM_MODEL=openai/gpt-oss-120b" in active
    assert "LLM_BASE_URL=https://api.groq.com/openai/v1" in active
    assert "# LLM_MODEL=gemini-3.8-flash" in text
    assert "# LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/" in text
