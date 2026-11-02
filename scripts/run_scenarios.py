"""Run the conversation scenarios against the real LLM and export the example conversations.

Each scenario checks which tools were called and which texts must (not) appear (D-16).
Needs the LLM variables of `.env`; it does not run in CI.

Usage:
    uv run python scripts/run_scenarios.py [--only ID] [--pause 5] [--export] [--no-v3]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from emporio.agent import Agent, TraceEvent, load_system_prompt_template
from emporio.llm import LLMClient, LLMSettings, Message, OpenAICompatibleClient
from emporio.policies import PolicyBook
from emporio.services import STORE_TZ, StoreHours
from emporio.store_data import StoreData
from emporio.tools import ToolContext, ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "tests" / "scenarios.yaml"
FAILURES_DIR = ROOT / ".scenario-failures"  # transcripts of failing scenarios, not versioned
DEFAULT_PAUSE = 5.0  # AS-38: stay under free-tier rate limits


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    now: datetime
    messages: list[str]
    expected_tools: list[str] = field(default_factory=list)
    forbid_tools: bool = False
    must_include: list[str] = field(default_factory=list)
    must_not_include: list[str] = field(default_factory=list)
    first_reply_must_not_include: list[str] = field(default_factory=list)
    export_as: str | None = None
    tag: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scenario:
        now = datetime.fromisoformat(str(data["now"]))
        if now.tzinfo is None:
            now = now.replace(tzinfo=STORE_TZ)
        return cls(
            id=data["id"],
            title=data.get("title", data["id"]),
            now=now,
            messages=list(data["messages"]),
            expected_tools=list(data.get("expected_tools") or []),
            forbid_tools=bool(data.get("forbid_tools", False)),
            must_include=list(data.get("must_include") or []),
            must_not_include=list(data.get("must_not_include") or []),
            first_reply_must_not_include=list(data.get("first_reply_must_not_include") or []),
            export_as=data.get("export_as"),
            tag=data.get("tag"),
        )


@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    failures: list[str]
    turns: list[tuple[str, str, list[str]]]  # (customer, mel, tools called in that turn)
    tools_called: list[str]
    elapsed_seconds: float
    history: list[Message] = field(default_factory=list)  # with tool calls and results
    events: list[TraceEvent] = field(default_factory=list)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)
        if event.kind == "tool_call" and event.name:
            self.calls.append(event.name)


def load_scenarios(path: Path) -> list[Scenario]:
    with path.open(encoding="utf-8") as handle:
        return [Scenario.from_dict(item) for item in yaml.safe_load(handle)]


def select(items: Iterable[Scenario], only: str | None, include_v3: bool) -> list[Scenario]:
    chosen = [s for s in items if only is None or s.id == only]
    return [s for s in chosen if include_v3 or s.tag != "v3"]


def check(scenario: Scenario, replies: str, tools: list[str], first_reply: str = "") -> list[str]:
    failures = []
    for tool in scenario.expected_tools:
        if tool not in tools:
            failures.append(f"expected tool not called: {tool}")
    if scenario.forbid_tools and tools:
        failures.append(f"no tool allowed, called: {tools}")
    lowered = replies.lower()
    for needle in scenario.must_include:
        if needle.lower() not in lowered:
            failures.append(f"must include: {needle!r}")
    for needle in scenario.must_not_include:
        if needle.lower() in lowered:
            failures.append(f"must not include: {needle!r}")
    for needle in scenario.first_reply_must_not_include:
        if needle.lower() in first_reply.lower():
            failures.append(f"first reply must not include: {needle!r}")
    return failures


def run_scenario(
    scenario: Scenario,
    llm: LLMClient,
    data: StoreData,
    policies: PolicyBook,
    workdir: Path,
) -> ScenarioResult:
    started = time.monotonic()
    recorder = _Recorder()
    context = ToolContext(
        data=data,
        policies=policies,
        now=lambda: scenario.now,
        escalation_log=workdir / f"{scenario.id}-escalations.jsonl",
    )
    agent = Agent(
        llm=llm,
        registry=ToolRegistry.default(context),
        system_prompt_template=load_system_prompt_template(),
        clock=lambda: scenario.now,
        store_hours=StoreHours(),
        tracer=recorder,
    )
    turns = []
    for message in scenario.messages:
        before = len(recorder.calls)
        reply = agent.reply(message)
        turns.append((message, reply, recorder.calls[before:]))
    first_reply = turns[0][1] if turns else ""
    failures = check(scenario, "\n".join(t[1] for t in turns), recorder.calls, first_reply)
    return ScenarioResult(
        scenario_id=scenario.id,
        passed=not failures,
        failures=failures,
        turns=turns,
        tools_called=recorder.calls,
        elapsed_seconds=time.monotonic() - started,
        history=agent.history,
        events=recorder.events,
    )


def run_all(
    items: list[Scenario],
    run_one: Callable[[Scenario], Any],
    pause: float,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Any]:
    results = []
    for index, item in enumerate(items):
        if index > 0 and pause > 0:
            sleep(pause)
        results.append(run_one(item))
    return results


def export(
    result: ScenarioResult, scenario: Scenario, root: Path, model: str, run_date: str
) -> Path:
    assert scenario.export_as is not None
    path = root / scenario.export_as
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {scenario.title}",
        "",
        f"> Scenario `{scenario.id}` · model `{model}` · run on {run_date} · "
        f"store clock frozen at {scenario.now:%d/%m/%Y %H:%M} (America/Campo_Grande).",
        "> The text below is the agent's unedited output; tool calls are shown in italics.",
        "",
    ]
    for customer, mel, tools in result.turns:
        lines += [f"**Cliente:** {customer}", ""]
        if tools:
            lines += [f"_Tools: {', '.join(tools)}_", ""]
        lines += [f"**Mel:** {mel}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


_GUARD_EVENTS = {"protocol_guard", "language_guard", "alternatives_guard", "llm_error"}


def save_failure(
    result: ScenarioResult, scenario: Scenario, directory: Path, model: str, stamp: str
) -> Path:
    """Write the full transcript of a failing scenario (messages, tool calls and results)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}-{scenario.id}.md"
    lines = [
        f"# FAIL {scenario.id}: {scenario.title}",
        "",
        f"Model `{model}` · run {stamp} · store clock {scenario.now.isoformat()}",
        "",
        "## Failed checks",
        "",
        *[f"- {failure}" for failure in result.failures],
        "",
        "## Guards and errors (drafts truncated)",
        "",
        *[f"- {e.kind}: {e.summary}" for e in result.events if e.kind in _GUARD_EVENTS],
        "",
        "## Transcript",
        "",
    ]
    for message in result.history:
        lines.append(f"### {message.role}")
        lines.append("")
        if message.content:
            lines += [message.content, ""]
        for tool_call in message.tool_calls:
            lines += [f"- call `{tool_call.name}` {tool_call.arguments}"]
        if message.tool_calls:
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--only", default=None)
    default_pause = float(os.environ.get("SCENARIO_PAUSE_SECONDS", DEFAULT_PAUSE))
    parser.add_argument("--pause", type=float, default=default_pause)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--no-v3", dest="include_v3", action="store_false")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env", override=False)
    try:
        settings = LLMSettings.from_env()
    except ValueError as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 2
    llm = OpenAICompatibleClient(settings)
    data = StoreData.load(ROOT / "data" / "raw")
    policies = PolicyBook.load(ROOT / "data" / "policies.md")
    workdir = ROOT / "logs" / "scenarios"
    workdir.mkdir(parents=True, exist_ok=True)
    items = select(load_scenarios(args.file), args.only, args.include_v3)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    def run_one(item: Scenario) -> ScenarioResult:
        result = run_scenario(item, llm, data, policies, workdir)
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {item.id} ({result.elapsed_seconds:.1f} s) tools={result.tools_called}")
        for failure in result.failures:
            print(f"     - {failure}")
        if not result.passed:
            for customer, mel, _ in result.turns:
                print(f"     > {customer}\n     < {mel}")
            saved = save_failure(result, item, FAILURES_DIR, settings.model, stamp)
            print(f"     transcript {saved.relative_to(ROOT)}")
        if args.export and item.export_as and result.passed:
            path = export(result, item, ROOT, settings.model, date.today().strftime("%d/%m/%Y"))
            print(f"     exported {path.relative_to(ROOT)}")
        return result

    results = run_all(items, run_one, args.pause)
    failed = [r.scenario_id for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed; failed: {failed or 'none'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
