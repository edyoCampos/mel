"""Chat CLI: arguments, exit words, trace output and clear startup errors (D-07, D-14, NFR-07)."""

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from emporio import cli
from emporio.agent import TraceEvent
from emporio.services import STORE_TZ
from tests.conftest import ROOT

runner = CliRunner()
DATA_ARGS = ["--data-dir", str(ROOT / "data" / "raw"), "--policies", str(ROOT / "data" / "policies.md")]


class StubAgent:
    def __init__(self, tracer: Any) -> None:
        self.tracer = tracer
        self.messages: list[str] = []

    def reply(self, message: str) -> str:
        self.messages.append(message)
        if self.tracer is not None:
            self.tracer.emit(
                TraceEvent("tool_call", "get_product_details", {"query": "takamine"}, "", 0)
            )
            self.tracer.emit(TraceEvent("tool_result", "get_product_details", None, "ok {}", 412))
        return "O Takamine GD20 sai por R$ 2.199,00."


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    created: dict[str, Any] = {}

    def fake_build(
        data_dir: Path, policies_path: Path, now: datetime | None, tracer: Any
    ) -> StubAgent:
        created["agent"] = StubAgent(tracer)
        created["now"] = now
        return created["agent"]

    monkeypatch.setattr(cli, "build_agent", fake_build)
    return created


def test_parse_now_with_offset() -> None:
    value = cli.parse_now("2026-11-01T10:00:00-04:00")
    assert value.isoformat() == "2026-11-01T10:00:00-04:00"


def test_parse_now_without_offset_uses_store_timezone() -> None:
    value = cli.parse_now("2026-11-01T10:00")
    assert value.tzinfo == STORE_TZ
    assert value.utcoffset().total_seconds() == -4 * 3600  # type: ignore[union-attr]


def test_parse_now_invalid_exits_with_error(stub: dict[str, Any]) -> None:
    result = runner.invoke(cli.app, ["chat", "--now", "yesterday"])
    assert result.exit_code == 2


def test_chat_exits_on_sair(stub: dict[str, Any]) -> None:
    result = runner.invoke(cli.app, ["chat"], input="sair\n")
    assert result.exit_code == 0
    assert stub["agent"].messages == []


def test_chat_prints_agent_reply(stub: dict[str, Any]) -> None:
    result = runner.invoke(cli.app, ["chat", "--now", "2026-11-01T10:00"], input="oi\nsair\n")
    assert result.exit_code == 0
    assert "R$ 2.199,00" in result.output
    assert stub["now"] == datetime(2026, 11, 1, 10, 0, tzinfo=STORE_TZ)


def test_trace_flag_prints_tool_events(stub: dict[str, Any]) -> None:
    result = runner.invoke(cli.app, ["chat", "--trace"], input="takamine\nsair\n")
    assert "→ get_product_details" in result.output
    assert "412 ms" in result.output


def test_no_trace_by_default(stub: dict[str, Any]) -> None:
    result = runner.invoke(cli.app, ["chat"], input="takamine\nsair\n")
    assert "→ get_product_details" not in result.output


def test_missing_api_key_prints_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    result = runner.invoke(cli.app, ["chat", *DATA_ARGS], input="sair\n")
    assert result.exit_code == 2
    assert "LLM_API_KEY" in result.output
    assert "Traceback" not in result.output


def test_missing_data_dir_prints_clear_error(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["chat", "--data-dir", str(tmp_path / "nope")])
    assert result.exit_code == 2
    assert "Missing data file" in result.output
    assert "Traceback" not in result.output


def test_dotenv_does_not_override_existing_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("LLM_MODEL=from-dotenv\nLLM_BASE_URL=http://x\n")
    monkeypatch.setenv("LLM_MODEL", "from-env")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    cli.load_environment(tmp_path)
    assert os.environ["LLM_MODEL"] == "from-env"
    assert os.environ["LLM_BASE_URL"] == "http://x"
    monkeypatch.delenv("LLM_BASE_URL")
