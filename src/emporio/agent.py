"""The Mel's conversation loop: per-turn system prompt, tool calls and session history.

"The LLM decides what; the code decides how much": the model chooses tools and writes the
answer, while every amount comes from tool results already formatted (D-01, D-04).
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from typing import Any, Protocol

from emporio.llm import LLMClient, LLMError, Message, QuotaExceededError
from emporio.services import (
    CUSTOMER_LANGUAGE,
    STORE_TZ,
    Language,
    StoreHours,
    detect_language,
    mask_email,
    mask_phone,
)
from emporio.tools import ToolRegistry, mask_contacts

FALLBACK_ERROR = (
    "Desculpa, tive um problema técnico agora e não consegui consultar o sistema. "
    "Você pode falar com a nossa equipe pelo (67) 3341-4444 ou pelo e-mail "
    "contato@emporiodamusica.com.br."
)
FALLBACK_LIMIT = (
    "Hmm, essa pergunta ficou mais complexa do que eu consigo resolver por aqui. "
    "Quer que eu passe para um atendente? Você também pode ligar no (67) 3341-4444."
)
FALLBACK_QUOTA = "O provedor gratuito atingiu o limite de uso. Tente de novo em {wait}."
EMPTY_MESSAGE_REPLY = "Oi! Sou a Mel, da Empório da Música. Como posso te ajudar?"
# Protocol guard (D-12 exception): a protocol exists only when escalate_to_human
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
# Language guard (D-12 exception): an English customer message answered in Portuguese gets one retry
# with this instruction (sent, never stored); a second Portuguese draft is kept.
LANGUAGE_NUDGE = (
    "The customer's latest message is in English. Rewrite your reply entirely in English, "
    "keeping every code, date and amount exactly as the tools returned them."
)

# Alternatives guard (D-12 exception): when get_product_details returned an unavailable
# product with alternatives and the reply names none of them, one retry with this instruction
# (sent, never stored); a second miss is kept.
ALTERNATIVES_NUDGE = (
    "The product you looked up is unavailable. Present the alternatives returned by the tool "
    "(the `alternatives` field of get_product_details), with their names and current prices, "
    "without asking first."
)
_ALTERNATIVE_NAME_WORDS = 2  # brand and model ("Shelby SGD-195E"), enough to identify one


def _unavailable_alternatives(payload: dict[str, Any]) -> list[str]:
    """Names of the alternatives returned for an unavailable product, or [] otherwise."""
    if payload.get("status") != "found":
        return []
    product = payload.get("product") or {}
    if product.get("availability") == "available":
        return []
    return [str(item.get("name", "")) for item in payload.get("alternatives") or []]


def _plain(text: str) -> str:
    return text.translate(_PLAIN_HYPHENS).translate(_PLAIN_SPACES).casefold()


def _names_any(content: str, names: list[str]) -> bool:
    plain = _plain(content)
    return any(
        _plain(" ".join(name.split()[:_ALTERNATIVE_NAME_WORDS])) in plain
        for name in names
        if name.strip()
    )


def load_system_prompt_template() -> str:
    """The Mel's system prompt (prompts/system.md), with {now_local}, {weekday}, {store_status}."""
    return files("emporio").joinpath("prompts/system.md").read_text(encoding="utf-8")


_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_SUMMARY_LIMIT = 160


@dataclass(frozen=True)
class TraceEvent:
    kind: str  # "tool_call" | "tool_result" | "llm_error" | "limit_reached" | "protocol_guard"
    # | "language_guard" | "alternatives_guard"
    name: str | None
    arguments: dict[str, Any] | None  # contacts masked (NFR-03)
    summary: str
    elapsed_ms: int


class Tracer(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


# U+2010 HYPHEN and U+2011 NON-BREAKING HYPHEN, written by some models in codes such as
# "79202-333" or "AC-39"; replies keep the data's ASCII hyphen.
_PLAIN_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-"})
# U+00A0 and U+202F (no-break spaces) become plain spaces, and "12 %" becomes "12%", so
# "R$ 429,90" reads as the tools wrote it.
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
    if isinstance(masked.get("customer_contact"), str):
        masked["customer_contact"] = mask_contacts(masked["customer_contact"])
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
        self._protocols: set[str] = set()  # returned by escalate_to_human
        # Customer language, kept when a message is ambiguous ("ok"); Portuguese by default
        self._language: Language = "pt"

    @property
    def history(self) -> list[Message]:
        return list(self._history)

    def reset(self) -> None:
        self._history.clear()
        self._protocols.clear()
        self._language = "pt"

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
            store_status=self._store_hours.describe(now, self._language),
        )
        return Message(role="system", content=content)

    def reply(self, user_message: str) -> str:
        if not user_message.strip():
            return EMPTY_MESSAGE_REPLY
        self._history.append(Message(role="user", content=user_message.strip()))
        self._language = detect_language(user_message) or self._language
        # Tools write customer-facing phrases in this language
        token = CUSTOMER_LANGUAGE.set(self._language)
        try:
            return self._run_turn()
        finally:
            CUSTOMER_LANGUAGE.reset(token)

    def _run_turn(self) -> str:
        calls_done = 0
        nudged = False
        language_nudged = False
        alternatives_nudged = False
        # One list of alternative names per unavailable product looked up in this turn
        unavailable: list[list[str]] = []
        english = self._language == "en"
        protocols_before = len(self._protocols)
        schemas = self._registry.schemas()
        while True:
            started = time.monotonic()
            extra = []
            if nudged and len(self._protocols) == protocols_before:
                extra = [Message(role="system", content=PROTOCOL_NUDGE)]
            if alternatives_nudged:
                extra.append(Message(role="system", content=ALTERNATIVES_NUDGE))
            if language_nudged:
                extra.append(Message(role="system", content=LANGUAGE_NUDGE))
            try:
                response = self._llm.complete(
                    [self._system_prompt(), *self._history, *extra], schemas
                )
            except LLMError as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                self._emit(TraceEvent("llm_error", None, None, str(exc)[:_SUMMARY_LIMIT], elapsed))
                if isinstance(exc, QuotaExceededError):
                    minutes = max(1, math.ceil(exc.retry_after_seconds / 60))
                    wait = "1 minuto" if minutes == 1 else f"{minutes} minutos"
                    return self._finish(FALLBACK_QUOTA.format(wait=wait))
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
                if not alternatives_nudged and any(
                    not _names_any(content, names) for names in unavailable
                ):
                    self._emit(
                        TraceEvent("alternatives_guard", None, None, content[:_SUMMARY_LIMIT], 0)
                    )
                    alternatives_nudged = True
                    continue
                if english and not language_nudged and detect_language(content) == "pt":
                    self._emit(
                        TraceEvent("language_guard", None, None, content[:_SUMMARY_LIMIT], 0)
                    )
                    language_nudged = True
                    continue
                return self._finish(content)
            if calls_done + len(response.tool_calls) > self.MAX_TOOL_CALLS_PER_TURN:
                refused = ", ".join(
                    f"{c.name} {json.dumps(_masked_arguments(c.arguments), ensure_ascii=False)}"
                    for c in response.tool_calls
                )
                summary = f"{calls_done} tool calls; refused: {refused}"
                self._emit(TraceEvent("limit_reached", None, None, summary, 0))
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
                if tool_call.name == "get_product_details" and result.ok:
                    names = _unavailable_alternatives(result.payload)
                    if names:
                        unavailable.append(names)
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
