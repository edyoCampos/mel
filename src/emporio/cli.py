"""Terminal chat with the Mel (D-14). `--trace` shows each tool call; `--now` freezes the clock."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from emporio.agent import Agent, TraceEvent, Tracer, load_system_prompt_template
from emporio.llm import LLMSettings, OpenAICompatibleClient
from emporio.policies import PolicyBook, PolicyLoadError
from emporio.services import STORE_TZ, StoreHours
from emporio.store_data import DataLoadError, StoreData
from emporio.tools import ToolContext, ToolRegistry

EXIT_WORDS = {"sair", "exit", "quit"}
LOG_PATH = Path("logs") / "escalations.jsonl"

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Mel, Empório da Música.")
console = Console()


@app.callback()
def _main() -> None:
    """Mel, the customer service agent of Empório da Música."""


def parse_now(value: str) -> datetime:
    """ISO 8601; without an offset the time is read in America/Campo_Grande (UTC-04:00)."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid ISO 8601 date-time: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=STORE_TZ)


def load_environment(directory: Path) -> None:
    """Load `.env` from the directory without overriding variables already set."""
    load_dotenv(dotenv_path=directory / ".env", override=False)


class RichTracer:
    def __init__(self, out: Console) -> None:
        self._out = out

    def emit(self, event: TraceEvent) -> None:
        if event.kind == "tool_call":
            args = json.dumps(event.arguments or {}, ensure_ascii=False)
            self._out.print(f"[dim]→ {event.name} {escape(args)}[/dim]")
        elif event.kind == "tool_result":
            self._out.print(f"[dim]← {escape(event.summary)} · {event.elapsed_ms} ms[/dim]")
        else:
            self._out.print(f"[dim]! {event.kind}: {escape(event.summary)}[/dim]")


def build_agent(
    data_dir: Path, policies_path: Path, now: datetime | None, tracer: Tracer | None
) -> Agent:
    data = StoreData.load(data_dir)
    policies = PolicyBook.load(policies_path)
    load_environment(Path.cwd())
    settings = LLMSettings.from_env()

    def clock() -> datetime:
        return now if now is not None else datetime.now(STORE_TZ)

    context = ToolContext(data=data, policies=policies, now=clock, escalation_log=LOG_PATH)
    return Agent(
        llm=OpenAICompatibleClient(settings),
        registry=ToolRegistry.default(context),
        system_prompt_template=load_system_prompt_template(),
        clock=clock,
        store_hours=StoreHours(),
        tracer=tracer,
    )


@app.command()
def chat(
    trace: Annotated[bool, typer.Option("--trace", help="Show each tool call and result.")] = False,
    now: Annotated[
        str | None,
        typer.Option("--now", help="Freeze the clock, e.g. 2026-11-01T10:00:00-04:00."),
    ] = None,
    data_dir: Annotated[Path, typer.Option("--data-dir")] = Path("data/raw"),
    policies: Annotated[Path, typer.Option("--policies")] = Path("data/policies.md"),
) -> None:
    """Chat with the Mel in the terminal. Type 'sair' to quit."""
    frozen = parse_now(now) if now else None
    tracer = RichTracer(console) if trace else None
    try:
        agent = build_agent(data_dir, policies, frozen, tracer)
    except (DataLoadError, PolicyLoadError, ValueError) as exc:
        console.print(f"[red]Startup error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=2) from None
    console.print("[bold]Mel[/bold] · Empório da Música — digite 'sair' para encerrar.")
    while True:
        try:
            message = console.input("[bold cyan]Você:[/bold cyan] ")
        except (EOFError, KeyboardInterrupt):
            break
        if message.strip().lower() in EXIT_WORDS:
            break
        reply = agent.reply(message)
        console.print(Panel(escape(reply), title="Mel", title_align="left", border_style="green"))
    console.print("Até logo!")
