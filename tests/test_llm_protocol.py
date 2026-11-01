"""LLMClient protocol, settings from the environment and the FakeLLM (NFR-05, NFR-06)."""

import pytest

from emporio.llm import LLMClient, LLMError, LLMSettings, Message
from tests.fakes import FakeLLM, text

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_settings_from_env_reads_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", GEMINI_URL)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "gemini-3.8-flash")
    settings = LLMSettings.from_env()
    assert settings.base_url == GEMINI_URL
    assert settings.api_key == "test-key"
    assert settings.model == "gemini-3.8-flash"
    assert settings.temperature == 0.0
    assert settings.max_attempts == 3


def test_settings_from_env_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", GEMINI_URL)
    monkeypatch.setenv("LLM_MODEL", "gemini-3.8-flash")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        LLMSettings.from_env()


def test_settings_lists_every_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="LLM_BASE_URL.*LLM_API_KEY.*LLM_MODEL"):
        LLMSettings.from_env()


def test_fake_llm_satisfies_protocol() -> None:
    client: LLMClient = FakeLLM([text("Oi!"), LLMError("down")])
    response = client.complete([Message(role="user", content="oi")])
    assert response.content == "Oi!"
    with pytest.raises(LLMError):
        client.complete([Message(role="user", content="de novo")])
