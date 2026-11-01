"""The Mel's conversation loop: per-turn system prompt, tool calls and session history.

"The LLM decides what; the code decides how much": the model chooses tools and writes the
answer, while every amount comes from tool results already formatted (D-01, D-04).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from typing import Any, Protocol

from emporio.llm import LLMClient, LLMError, Message
from emporio.services import STORE_TZ, StoreHours, mask_email, mask_phone
from emporio.tools import ToolRegistry

FALLBACK_ERROR = (
    "Desculpa, tive um problema técnico agora e não consegui consultar o sistema. "
    "Você pode falar com a nossa equipe pelo (67) 3341-4444 ou pelo e-mail "
    "contato@emporiodamusica.com.br."
)
FALLBACK_LIMIT = (
    "Hmm, essa pergunta ficou mais complexa do que eu consigo resolver por aqui. "
    "Quer que eu passe para um atendente? Você também pode ligar no (67) 3341-4444."
)
EMPTY_MESSAGE_REPLY = "Oi! Sou a Mel, da Empório da Música. Como posso te ajudar?"


def load_system_prompt_template() -> str:
    """The Mel's system prompt (prompts/system.md), with {now_local}, {weekday}, {store_status}."""
    return files("emporio").joinpath("prompts/system.md").read_text(encoding="utf-8")


_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_SUMMARY_LIMIT = 160


@dataclass(frozen=True)
class TraceEvent:
    kind: str  # "tool_call" | "tool_result" | "llm_error" | "limit_reached"
    name: str | None
    arguments: dict[str, Any] | None  # contacts masked (NFR-03)
    summary: str
    elapsed_ms: int


class Tracer(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


# U+2010 HYPHEN and U+2011 NON-BREAKING HYPHEN, written by some models in codes such as
# "79202-333" or "AC-39"; replies keep the data's ASCII hyphen (AS-73).
_PLAIN_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-"})


def _masked_arguments(raw: str) -> dict[str, Any] | None:
    try:
        arguments = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(arguments, dict):
        return None
    masked = dict(arguments)
    if isinstance(masked.get("email"), str):
        masked["email"] = mask_email(masked["email"])
    if isinstance(masked.get("phone"), str):
        masked["phone"] = mask_phone(masked["phone"])
    return masked


class Agent:
    MAX_TOOL_CALLS_PER_TURN = 6  # NFR-09

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        system_prompt_template: str,
        clock: Callable[[], datetime],
        store_hours: StoreHours,
        tracer: Tracer | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._template = system_prompt_template
        self._clock = clock
        self._store_hours = store_hours
        self._tracer = tracer
        self._history: list[Message] = []

    @property
    def history(self) -> list[Message]:
        return list(self._history)

    def reset(self) -> None:
        self._history.clear()

    def _emit(self, event: TraceEvent) -> None:
        if self._tracer is not None:
            self._tracer.emit(event)

    def _system_prompt(self) -> Message:
        now = self._clock().astimezone(STORE_TZ)
        content = self._template.format(
            now_local=now.strftime("%d/%m/%Y %H:%M"),
            weekday=_WEEKDAYS[now.weekday()],
            store_status=self._store_hours.describe(now),
        )
        return Message(role="system", content=content)

    def reply(self, user_message: str) -> str:
        if not user_message.strip():
            return EMPTY_MESSAGE_REPLY
        self._history.append(Message(role="user", content=user_message.strip()))
        calls_done = 0
        schemas = self._registry.schemas()
        while True:
            started = time.monotonic()
            try:
                response = self._llm.complete([self._system_prompt(), *self._history], schemas)
            except LLMError as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                self._emit(TraceEvent("llm_error", None, None, str(exc)[:_SUMMARY_LIMIT], elapsed))
                return self._finish(FALLBACK_ERROR)
            if not response.tool_calls:
                return self._finish(response.content or "")
            if calls_done + len(response.tool_calls) > self.MAX_TOOL_CALLS_PER_TURN:
                self._emit(TraceEvent("limit_reached", None, None, f"{calls_done} tool calls", 0))
                return self._finish(FALLBACK_LIMIT)
            self._history.append(
                Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
            )
            for tool_call in response.tool_calls:
                calls_done += 1
                arguments = _masked_arguments(tool_call.arguments)
                self._emit(TraceEvent("tool_call", tool_call.name, arguments, "", 0))
                started = time.monotonic()
                result = self._registry.dispatch(tool_call.name, tool_call.arguments)
                elapsed = int((time.monotonic() - started) * 1000)
                payload = result.to_json()
                status = "ok" if result.ok else f"error:{result.payload.get('error')}"
                self._emit(
                    TraceEvent(
                        "tool_result",
                        tool_call.name,
                        None,
                        f"{status} {payload[:_SUMMARY_LIMIT]}",
                        elapsed,
                    )
                )
                self._history.append(
                    Message(role="tool", content=payload, tool_call_id=tool_call.id)
                )

    def _finish(self, content: str) -> str:
        content = content.translate(_PLAIN_HYPHENS)
        self._history.append(Message(role="assistant", content=content))
        return content
