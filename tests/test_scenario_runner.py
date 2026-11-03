"""Scenario runner checks, pause and export (D-16), with a scripted LLM."""

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from emporio.policies import PolicyBook
from emporio.store_data import StoreData
from tests.conftest import ROOT
from tests.fakes import FakeLLM, call, text, tool_calls


def load_runner() -> ModuleType:
    path = ROOT / "scripts" / "run_scenarios.py"
    spec = importlib.util.spec_from_file_location("run_scenarios", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_scenarios"] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def scenario(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "id": "takamine",
        "title": "Price of the Takamine GD20",
        "now": "2026-11-01T10:00:00-04:00",
        "messages": ["quanto custa o takamine gd 20?"],
        "expected_tools": ["get_product_details"],
        "must_include": ["R$ 2.199,00"],
        "must_not_include": ["R$ 1.935,12"],
    }
    fields.update(overrides)
    return runner.Scenario.from_dict(fields)


@pytest.fixture
def run(
    store_data: StoreData, policy_book: PolicyBook, tmp_path: Path
) -> Callable[[Any, FakeLLM], Any]:
    def execute(item: Any, llm: FakeLLM) -> Any:
        return runner.run_scenario(item, llm, store_data, policy_book, tmp_path)

    return execute


PRICE_SCRIPT = [
    tool_calls(call("get_product_details", '{"query": "takamine gd 20"}')),
    text("O Takamine GD20 sai por R$ 2.199,00."),
]


def test_passing_scenario(run: Any) -> None:
    result = run(scenario(), FakeLLM(PRICE_SCRIPT))
    assert result.passed, result.failures
    assert result.tools_called == ["get_product_details"]


def test_expected_tool_missing_fails(run: Any) -> None:
    result = run(scenario(), FakeLLM([text("Custa R$ 2.199,00.")]))
    assert not result.passed
    assert any("get_product_details" in f for f in result.failures)


def test_forbid_tools_fails_on_any_call(run: Any) -> None:
    item = scenario(expected_tools=[], forbid_tools=True)
    assert not run(item, FakeLLM(PRICE_SCRIPT)).passed


def test_must_include_is_case_insensitive(run: Any) -> None:
    item = scenario(must_include=["r$ 2.199,00", "TAKAMINE"])
    assert run(item, FakeLLM(PRICE_SCRIPT)).passed


def test_must_not_include_fails(run: Any) -> None:
    item = scenario(must_not_include=["takamine gd20 sai"])
    result = run(item, FakeLLM(PRICE_SCRIPT))
    assert not result.passed
    assert any("must not include" in f for f in result.failures)


def test_first_reply_must_not_include_checks_only_the_first_reply(run: Any) -> None:
    item = scenario(
        messages=["posso devolver?", "pedido 7"],
        expected_tools=[],
        must_include=[],
        first_reply_must_not_include=["pode devolver, sim"],
    )
    later = run(item, FakeLLM([text("Preciso do pedido."), text("Pode devolver, sim.")]))
    assert later.passed, later.failures
    first = run(item, FakeLLM([text("Pode devolver, sim!"), text("Ok.")]))
    assert not first.passed
    assert any("first reply must not include" in f for f in first.failures)


def test_must_include_at_least_counts_distinct_texts() -> None:
    item = scenario(must_include=[], must_include_at_least={"count": 2, "of": ["A1", "B2", "C3"]})
    assert runner.check(item, "a1 and c3", []) == ["expected tool not called: get_product_details"]
    failures = runner.check(item, "only a1", [])
    assert "must include at least 2 of ['A1', 'B2', 'C3'], found 1" in failures


def test_pause_between_scenarios() -> None:
    sleeps: list[float] = []
    items = [scenario(id="a"), scenario(id="b"), scenario(id="c")]
    seen: list[str] = []
    runner.run_all(items, lambda s: seen.append(s.id), pause=5.0, sleep=sleeps.append)
    assert seen == ["a", "b", "c"]
    assert sleeps == [5.0, 5.0]


def test_no_v3_skips_tagged_scenarios() -> None:
    items = [scenario(id="a"), scenario(id="v3", tag="v3")]
    assert [s.id for s in runner.select(items, only=None, include_v3=False)] == ["a"]
    assert [s.id for s in runner.select(items, only="v3", include_v3=True)] == ["v3"]


def test_export_writes_header_and_transcript(run: Any, tmp_path: Path) -> None:
    item = scenario(export_as="examples/02-price.md")
    result = run(item, FakeLLM(PRICE_SCRIPT))
    path = runner.export(result, item, tmp_path, model="test-model", run_date="03/11/2026")
    content = path.read_text(encoding="utf-8")
    assert path == tmp_path / "examples" / "02-price.md"
    assert "Price of the Takamine GD20" in content
    assert "test-model" in content
    assert "unedited" in content
    assert "quanto custa o takamine gd 20?" in content
    assert "R$ 2.199,00" in content
    assert "get_product_details" in content


# ---------- Transcripts of failing scenarios ----------

FAILING_SCRIPT = [
    tool_calls(call("get_product_details", '{"query": "takamine gd 20"}')),
    text("O Takamine GD20 está em promoção."),
]


def test_failure_transcript_has_failures_tool_results_and_replies(run: Any, tmp_path: Path) -> None:
    item = scenario()
    result = run(item, FakeLLM(FAILING_SCRIPT))
    assert not result.passed
    path = runner.save_failure(
        result, item, tmp_path / "failures", model="test-model", stamp="20261102-1800"
    )
    content = path.read_text(encoding="utf-8")
    assert path == tmp_path / "failures" / "20261102-1800-takamine.md"
    assert "test-model" in content
    assert "must include: 'R$ 2.199,00'" in content
    assert "quanto custa o takamine gd 20?" in content
    assert '{"query": "takamine gd 20"}' in content  # tool call with its arguments
    assert '"price_display": "R$ 2.199,00"' in content  # full tool result
    assert "O Takamine GD20 está em promoção." in content


def test_failure_transcript_lists_guard_events(run: Any, tmp_path: Path) -> None:
    item = scenario(messages=["Hi! How much is the Takamine GD20?"])
    llm = FakeLLM([text("O Takamine GD20 custa caro."), text("It costs a lot.")])
    result = run(item, llm)
    path = runner.save_failure(result, item, tmp_path, model="m", stamp="s")
    content = path.read_text(encoding="utf-8")
    assert "language_guard" in content
    assert "O Takamine GD20 custa caro." in content  # the rejected draft


def test_failure_transcript_lists_the_tool_call_limit(run: Any, tmp_path: Path) -> None:
    item = scenario()
    loop = [tool_calls(call("get_product_details", '{"query": "gd20"}', f"c{i}")) for i in range(7)]
    result = run(item, FakeLLM(loop))
    content = runner.save_failure(result, item, tmp_path, model="m", stamp="s").read_text()
    assert "limit_reached" in content
    assert 'refused: get_product_details {"query": "gd20"}' in content


def test_each_run_starts_a_new_escalation_log(run: Any) -> None:
    args = (
        '{"reason": "complaint", "summary": "Tampo rachado.", "customer_contact": "Ana, ana@x.com"}'
    )
    item = scenario(
        id="complaint",
        messages=["Meu violão chegou rachado."],
        expected_tools=["escalate_to_human"],
        must_include=["EM-20261101-0001"],
        must_not_include=[],
    )
    for _ in range(2):
        llm = FakeLLM([tool_calls(call("escalate_to_human", args)), text("EM-20261101-0001")])
        result = run(item, llm)
        assert result.passed, result.failures
        protocol = '"protocol": "EM-20261101-0001"'
        assert any(protocol in (m.content or "") for m in result.history if m.role == "tool")


def test_failure_transcripts_are_not_versioned() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert runner.FAILURES_DIR.relative_to(ROOT).as_posix() + "/" in ignored
