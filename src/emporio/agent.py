"""The Mel's conversation loop: per-turn system prompt, tool calls and session history.

"The LLM decides what; the code decides how much": the model chooses tools and writes the
answer, while every amount comes from tool results already formatted (D-01, D-04).
"""

from __future__ import annotations

import json
import re
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
# Protocol guard (D-12 exception, AS-80): a protocol exists only when escalate_to_human
# returned it. A reply that mentions one without that gets one retry with this instruction
# (sent, never stored in the history); a second miss returns the store contacts.
PROTOCOL_NUDGE = (
    "Your last draft mentioned a protocol, but escalate_to_human was not called and no "
    "protocol exists. Call escalate_to_human now if the case needs a human; otherwise answer "
    "without mentioning any protocol."
)
FALLBACK_NO_PROTOCOL = (
    "Sinto muito por isso. Para registrar o seu caso com a nossa equipe, fale com a gente "
    "pelo WhatsApp (67) 3341-4444 ou pelo e-mail contato@emporiodamusica.com.br."
)
_PROTOCOL_WORD = re.compile(r"protocol", re.IGNORECASE)
_PROTOCOL_CODE = re.compile(r"EM-\d{8}-\d{4}")
# Language guard (AS-82): an English customer message answered in Portuguese gets one retry
# with this instruction (sent, never stored); a second Portuguese draft is kept.
LANGUAGE_NUDGE = (
    "The customer's latest message is in English. Rewrite your reply entirely in English, "
    "keeping every code, date and amount exactly as the tools returned them."
)
# Function words that exist in only one of the two languages ("a", "no", "do" are in both).
_EN_WORDS = frozenset(
    [
        "the",
        "is",
        "are",
        "was",
        "my",
        "your",
        "you",
        "i",
        "me",
        "where",
        "what",
        "when",
        "how",
        "why",
        "please",
        "yes",
        "thanks",
        "thank",
        "hi",
        "hello",
        "and",
        "to",
        "for",
        "with",
        "can",
        "could",
        "would",
        "will",
        "it",
        "this",
        "that",
        "have",
        "has",
        "order",
        "ticket",
        "of",
    ]
)
_PT_WORDS = frozenset(
    [
        "o",
        "os",
        "as",
        "de",
        "da",
        "das",
        "dos",
        "que",
        "é",
        "não",
        "seu",
        "sua",
        "meu",
        "minha",
        "para",
        "com",
        "um",
        "uma",
        "você",
        "está",
        "estou",
        "por",
        "em",
        "na",
        "mas",
        "já",
        "oi",
        "olá",
        "obrigado",
        "obrigada",
        "pedido",
        "tudo",
        "bem",
        "cadê",
        "quero",
        "posso",
    ]
)
_WORD = re.compile(r"[^\W\d_]+")


def _language_score(text: str) -> tuple[int, int]:
    words = _WORD.findall(text.lower())
    return sum(w in _EN_WORDS for w in words), sum(w in _PT_WORDS for w in words)


def _is_english(text: str) -> bool:
    english, portuguese = _language_score(text)
    return english >= 2 and english > portuguese


def _is_portuguese(text: str) -> bool:
    english, portuguese = _language_score(text)
    return portuguese >= 2 and portuguese > english


def load_system_prompt_template() -> str:
    """The Mel's system prompt (prompts/system.md), with {now_local}, {weekday}, {store_status}."""
    return files("emporio").joinpath("prompts/system.md").read_text(encoding="utf-8")


_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_SUMMARY_LIMIT = 160


@dataclass(frozen=True)
class TraceEvent:
    kind: str  # "tool_call" | "tool_result" | "llm_error" | "limit_reached" | "protocol_guard"
    # | "language_guard"
    name: str | None
    arguments: dict[str, Any] | None  # contacts masked (NFR-03)
    summary: str
    elapsed_ms: int


class Tracer(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


# U+2010 HYPHEN and U+2011 NON-BREAKING HYPHEN, written by some models in codes such as
# "79202-333" or "AC-39"; replies keep the data's ASCII hyphen (AS-73).
_PLAIN_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-"})
# U+00A0 and U+202F (no-break spaces) become plain spaces, and "12 %" becomes "12%", so
# "R$ 429,90" reads as the tools wrote it (AS-74).
_PLAIN_SPACES = str.maketrans({"\u00a0": " ", "\u202f": " "})
_SPACED_PERCENT = re.compile(r"(\d) %")


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
        self._protocols: set[str] = set()  # returned by escalate_to_human (AS-80)

    @property
    def history(self) -> list[Message]:
        return list(self._history)

    def reset(self) -> None:
        self._history.clear()
        self._protocols.clear()

    def _invents_protocol(self, content: str) -> bool:
        if not _PROTOCOL_WORD.search(content):
            return False
        if not self._protocols:
            return True
        return any(code not in self._protocols for code in _PROTOCOL_CODE.findall(content))

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
        nudged = False
        language_nudged = False
        english = _is_english(user_message)
        protocols_before = len(self._protocols)
        schemas = self._registry.schemas()
        while True:
            started = time.monotonic()
            extra = []
            if nudged and len(self._protocols) == protocols_before:
                extra = [Message(role="system", content=PROTOCOL_NUDGE)]
            if language_nudged:
                extra.append(Message(role="system", content=LANGUAGE_NUDGE))
            try:
                response = self._llm.complete(
                    [self._system_prompt(), *self._history, *extra], schemas
                )
            except LLMError as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                self._emit(TraceEvent("llm_error", None, None, str(exc)[:_SUMMARY_LIMIT], elapsed))
                return self._finish(FALLBACK_ERROR)
            if not response.tool_calls:
                content = response.content or ""
                if self._invents_protocol(content):
                    self._emit(
                        TraceEvent("protocol_guard", None, None, content[:_SUMMARY_LIMIT], 0)
                    )
                    if nudged:
                        return self._finish(FALLBACK_NO_PROTOCOL)
                    nudged = True
                    continue
                if english and not language_nudged and _is_portuguese(content):
                    self._emit(
                        TraceEvent("language_guard", None, None, content[:_SUMMARY_LIMIT], 0)
                    )
                    language_nudged = True
                    continue
                return self._finish(content)
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
                if tool_call.name == "escalate_to_human" and result.ok:
                    self._protocols.add(str(result.payload.get("protocol")))
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
        content = _SPACED_PERCENT.sub(
            r"\1%", content.translate(_PLAIN_HYPHENS).translate(_PLAIN_SPACES)
        )
        self._history.append(Message(role="assistant", content=content))
        return content
