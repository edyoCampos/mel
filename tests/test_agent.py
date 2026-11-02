"""Agent tool-calling loop with a scripted LLM (D-01, D-18, FR-14, NFR-07, NFR-08, NFR-09)."""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from emporio.agent import (
    FALLBACK_ERROR,
    FALLBACK_LIMIT,
    FALLBACK_NO_PROTOCOL,
    LANGUAGE_NUDGE,
    PROTOCOL_NUDGE,
    Agent,
    TraceEvent,
)
from emporio.llm import LLMError
from emporio.services import STORE_TZ, StoreHours
from emporio.tools import (
    EscalateToHumanTool,
    GetOrderStatusTool,
    GetProductDetailsTool,
    Tool,
    ToolContext,
    ToolRegistry,
)
from tests.fakes import FakeLLM, call, text, tool_calls

TEMPLATE = "You are Mel.\nNow: {weekday}, {now_local}.\n{store_status}"
SUNDAY = datetime(2026, 11, 1, 10, 0, tzinfo=STORE_TZ)
TUESDAY = datetime(2026, 11, 3, 10, 0, tzinfo=STORE_TZ)


class Recorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)


class PingArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PingTool(Tool):
    name = "ping"
    description = "Ping."
    args_model = PingArgs

    def run(self, args: BaseModel) -> dict[str, Any]:
        return {"pong": True}


@pytest.fixture
def build(make_context: Callable[..., ToolContext]) -> Callable[..., tuple[Agent, Recorder]]:
    def make(
        llm: FakeLLM, clock: Callable[[], datetime] = lambda: SUNDAY
    ) -> tuple[Agent, Recorder]:
        context = make_context()
        registry = ToolRegistry(
            [GetProductDetailsTool(context), GetOrderStatusTool(context), PingTool(context)]
        )
        recorder = Recorder()
        agent = Agent(llm, registry, TEMPLATE, clock, StoreHours(), tracer=recorder)
        return agent, recorder

    return make


def sent_system(llm: FakeLLM, index: int = 0) -> str:
    messages, _ = llm.requests[index]
    assert messages[0].role == "system"
    return messages[0].content or ""


def test_reply_without_tools_returns_text(build: Any) -> None:
    llm = FakeLLM([text("Oi! Sou a Mel.")])
    agent, _ = build(llm)
    assert agent.reply("oi") == "Oi! Sou a Mel."
    assert [m.role for m in agent.history] == ["user", "assistant"]


def test_reply_uses_plain_hyphens(build: Any) -> None:
    """Some models write U+2010/U+2011 hyphens; the customer must be able to copy
    "79202-333" or "AC-39" as typed in the data (AS-73)."""
    llm = FakeLLM([text("CEP 79202\u2011333, Tagima AC\u201039")])
    agent, _ = build(llm)
    assert agent.reply("oi") == "CEP 79202-333, Tagima AC-39"
    assert agent.history[-1].content == "CEP 79202-333, Tagima AC-39"


def test_reply_uses_plain_spaces_and_tight_percent(build: Any) -> None:
    """gpt-oss writes U+202F/U+00A0 spaces and "12 %"; replies keep the data's plain
    "R$ 429,90" and the Brazilian "12%" (AS-74)."""
    llm = FakeLLM([text("R$\u202f429,90, 10\u00a0dias úteis, 12\u202f% e 20 %")])
    agent, _ = build(llm)
    assert agent.reply("oi") == "R$ 429,90, 10 dias úteis, 12% e 20%"


def test_system_prompt_has_time_and_store_status(build: Any) -> None:
    llm = FakeLLM([text("ok")])
    agent, _ = build(llm)
    agent.reply("estão abertos?")
    system = sent_system(llm)
    assert "Sunday, 01/11/2026 10:00" in system
    assert "CLOSED" in system
    assert "terça-feira, 03/11/2026, às 09:00" in system


def test_system_prompt_is_rebuilt_each_turn(build: Any) -> None:
    times = iter([SUNDAY, TUESDAY])
    llm = FakeLLM([text("a"), text("b")])
    agent, _ = build(llm, clock=lambda: next(times))
    agent.reply("um")
    agent.reply("dois")
    assert "CLOSED" in sent_system(llm, 0)
    assert "OPEN" in sent_system(llm, 1)


def test_system_prompt_not_stored_in_history(build: Any) -> None:
    agent, _ = build(FakeLLM([text("ok")]))
    agent.reply("oi")
    assert all(m.role != "system" for m in agent.history)


def test_tool_call_then_answer(build: Any) -> None:
    llm = FakeLLM(
        [
            tool_calls(call("get_product_details", '{"query": "takamine gd 20"}')),
            text("O Takamine GD20 sai por R$ 2.199,00."),
        ]
    )
    agent, _ = build(llm)
    assert agent.reply("quanto custa o takamine?") == "O Takamine GD20 sai por R$ 2.199,00."
    roles = [m.role for m in agent.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert agent.history[2].tool_call_id == "call_1"


def test_tool_result_is_sent_back_to_llm(build: Any) -> None:
    llm = FakeLLM([tool_calls(call("get_product_details", '{"product_id": 95}')), text("ok")])
    agent, _ = build(llm)
    agent.reply("takamine?")
    messages, tools = llm.requests[1]
    assert tools is not None and len(tools) == 3
    tool_message = messages[-1]
    assert tool_message.role == "tool"
    assert "R$ 2.199,00" in (tool_message.content or "")


def test_multiple_tool_calls_in_one_response(build: Any) -> None:
    llm = FakeLLM(
        [
            tool_calls(
                call("get_product_details", '{"product_id": 95}', "a"),
                call("get_product_details", '{"product_id": 121}', "b"),
            ),
            text("ok"),
        ]
    )
    agent, _ = build(llm)
    agent.reply("compare")
    tool_messages = [m for m in agent.history if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["a", "b"]
    assert "R$ 439,20" in (tool_messages[1].content or "")


def test_invalid_tool_arguments_are_returned_to_llm(build: Any) -> None:
    llm = FakeLLM(
        [
            tool_calls(call("get_product_details", '{"product_id": "x"}', "bad")),
            tool_calls(call("get_product_details", '{"product_id": 95}', "good")),
            text("Agora sim."),
        ]
    )
    agent, _ = build(llm)
    assert agent.reply("takamine") == "Agora sim."
    first_result = json.loads(llm.requests[1][0][-1].content or "{}")
    assert first_result["error"] == "invalid_arguments"


def test_tool_call_limit_returns_fallback(build: Any) -> None:
    llm = FakeLLM([tool_calls(call("ping", "{}", f"c{i}")) for i in range(10)])
    agent, recorder = build(llm)
    assert agent.reply("loop") == FALLBACK_LIMIT
    executed = [e for e in recorder.events if e.kind == "tool_call"]
    assert len(executed) == 6
    assert recorder.events[-1].kind == "limit_reached"


def test_llm_error_returns_friendly_message(build: Any) -> None:
    agent, recorder = build(FakeLLM([LLMError("timeout")]))
    reply = agent.reply("oi")
    assert reply == FALLBACK_ERROR
    assert "(67) 3341-4444" in reply
    assert recorder.events[-1].kind == "llm_error"


def test_history_keeps_context_across_turns(build: Any) -> None:
    llm = FakeLLM([text("Qual o e-mail?"), text("ok")])
    agent, _ = build(llm)
    agent.reply("meu pedido é o 8")
    agent.reply("anacarol.ferreira@coldmail.com")
    second_request = llm.requests[1][0]
    contents = [m.content for m in second_request]
    assert "meu pedido é o 8" in contents
    assert "Qual o e-mail?" in contents


def test_trace_events_mask_contacts(build: Any) -> None:
    args = '{"order_id": 8, "email": "anacarol.ferreira@coldmail.com"}'
    llm = FakeLLM([tool_calls(call("get_order_status", args)), text("ok")])
    agent, recorder = build(llm)
    agent.reply("pedido 8")
    tool_call = next(e for e in recorder.events if e.kind == "tool_call")
    assert tool_call.arguments == {"order_id": 8, "email": "a***@coldmail.com"}
    assert all("anacarol" not in e.summary for e in recorder.events)


def test_empty_message_does_not_call_llm(build: Any) -> None:
    llm = FakeLLM([])
    agent, _ = build(llm)
    assert "Mel" in agent.reply("   ")
    assert llm.requests == []


def test_reset_clears_history(build: Any) -> None:
    agent, _ = build(FakeLLM([text("ok")]))
    agent.reply("oi")
    agent.reset()
    assert agent.history == []


# ---------- Protocol guard (D-12 exception, AS-80) ----------


@pytest.fixture
def build_esc(make_context: Callable[..., ToolContext]) -> Callable[..., tuple[Agent, Recorder]]:
    def make(llm: FakeLLM) -> tuple[Agent, Recorder]:
        context = make_context()
        registry = ToolRegistry([EscalateToHumanTool(context), PingTool(context)])
        recorder = Recorder()
        return Agent(llm, registry, TEMPLATE, lambda: SUNDAY, StoreHours(), recorder), recorder

    return make


ESCALATE = '{"reason": "complaint", "summary": "Violão chegou com o tampo rachado."}'


def test_invented_protocol_triggers_one_retry_that_escalates(build_esc: Any) -> None:
    llm = FakeLLM(
        [
            text("Abri o protocolo 2026-11-00123 para você."),
            tool_calls(call("escalate_to_human", ESCALATE)),
            text("Protocolo EM-20261101-0001, retorno em até 24 horas úteis."),
        ]
    )
    agent, recorder = build_esc(llm)
    assert agent.reply("Meu violão chegou rachado!") == (
        "Protocolo EM-20261101-0001, retorno em até 24 horas úteis."
    )
    retry_messages = llm.requests[1][0]
    assert retry_messages[-1].role == "system"
    assert retry_messages[-1].content == PROTOCOL_NUDGE
    assert all(m.content != PROTOCOL_NUDGE for m in agent.history)  # never stored
    assert all("00123" not in (m.content or "") for m in agent.history)
    assert "protocol_guard" in [e.kind for e in recorder.events]
    assert llm.requests[2][0][-1].role == "tool"  # nudge dropped once escalated


def test_invented_protocol_twice_returns_contacts_without_protocol(build_esc: Any) -> None:
    llm = FakeLLM([text("Seu protocolo é 123."), text("Protocolo: 456.")])
    agent, _ = build_esc(llm)
    reply = agent.reply("Quero reclamar.")
    assert reply == FALLBACK_NO_PROTOCOL
    assert "protocol" not in reply.lower()
    assert "(67) 3341-4444" in reply and "contato@emporiodamusica.com.br" in reply
    assert len(llm.requests) == 2


def test_english_protocol_word_is_guarded(build_esc: Any) -> None:
    llm = FakeLLM([text("Your protocol is 99."), text("I could not open a ticket now.")])
    agent, _ = build_esc(llm)
    assert agent.reply("Please open a ticket.") == "I could not open a ticket now."
    assert len(llm.requests) == 2


def test_protocol_after_real_escalation_passes(build_esc: Any) -> None:
    llm = FakeLLM(
        [
            tool_calls(call("escalate_to_human", ESCALATE)),
            text("Protocolo EM-20261101-0001."),
            text("O seu protocolo é o EM-20261101-0001."),
        ]
    )
    agent, _ = build_esc(llm)
    assert agent.reply("Quero reclamar.") == "Protocolo EM-20261101-0001."
    assert agent.reply("Qual era o protocolo mesmo?") == "O seu protocolo é o EM-20261101-0001."
    assert len(llm.requests) == 3  # no retry: the protocol came from the tool


def test_unknown_protocol_code_after_escalation_is_guarded(build_esc: Any) -> None:
    llm = FakeLLM(
        [
            tool_calls(call("escalate_to_human", ESCALATE)),
            text("Protocolo EM-20261101-0001."),
            text("Abri outro: protocolo EM-20261101-0777."),
            text("Pode falar com a equipe pelos contatos."),
        ]
    )
    agent, _ = build_esc(llm)
    agent.reply("Quero reclamar.")
    assert agent.reply("Abre outro?") == "Pode falar com a equipe pelos contatos."


def test_reply_without_protocol_is_untouched(build_esc: Any) -> None:
    llm = FakeLLM([text("Oi! Como posso ajudar?")])
    agent, _ = build_esc(llm)
    assert agent.reply("oi") == "Oi! Como posso ajudar?"
    assert len(llm.requests) == 1


# ---------- Language guard (AS-82) ----------


def test_portuguese_reply_to_english_message_gets_one_retry(build: Any) -> None:
    llm = FakeLLM(
        [
            text("Oi Ana, tudo bem? Seu pedido 8 já foi enviado, mas está atrasado."),
            text("Hi Ana! Your order 8 has shipped, but it is late."),
        ]
    )
    agent, recorder = build(llm)
    reply = agent.reply("Hi! Where is my order 8? My email is ana@example.com")
    assert reply == "Hi Ana! Your order 8 has shipped, but it is late."
    retry_messages = llm.requests[1][0]
    assert retry_messages[-1].role == "system"
    assert retry_messages[-1].content == LANGUAGE_NUDGE
    assert all(m.content != LANGUAGE_NUDGE for m in agent.history)  # never stored
    assert "language_guard" in [e.kind for e in recorder.events]


def test_second_portuguese_reply_is_kept(build: Any) -> None:
    llm = FakeLLM([text("Seu pedido já foi enviado."), text("O seu pedido está a caminho.")])
    agent, _ = build(llm)
    assert agent.reply("Yes, please open a ticket for me.") == "O seu pedido está a caminho."
    assert len(llm.requests) == 2


def test_english_reply_to_english_message_is_untouched(build: Any) -> None:
    llm = FakeLLM([text("Your order is on the way.")])
    agent, _ = build(llm)
    assert agent.reply("Where is my order?") == "Your order is on the way."
    assert len(llm.requests) == 1


def test_portuguese_message_keeps_portuguese_reply(build: Any) -> None:
    llm = FakeLLM([text("Seu pedido já foi enviado.")])
    agent, _ = build(llm)
    assert agent.reply("Cadê o meu pedido 8?") == "Seu pedido já foi enviado."
    assert len(llm.requests) == 1


def test_short_ambiguous_message_does_not_trigger_guard(build: Any) -> None:
    llm = FakeLLM([text("Oi! Como posso te ajudar?")])
    agent, _ = build(llm)
    assert agent.reply("ok") == "Oi! Como posso te ajudar?"
    assert len(llm.requests) == 1
