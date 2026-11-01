"""escalate_to_human tool (FR-13, FR-17, R-15, R-16, A-01, A-02, NFR-01, NFR-03)."""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from emporio.services import STORE_TZ
from emporio.tools import EscalateToHumanTool, ToolContext, ToolRegistry

SATURDAY_AFTERNOON = datetime(2026, 11, 7, 15, 0, tzinfo=STORE_TZ)
TUESDAY_MORNING = datetime(2026, 11, 3, 10, 0, tzinfo=STORE_TZ)
SUNDAY = datetime(2026, 11, 1, 10, 0, tzinfo=STORE_TZ)


def escalate(context: ToolContext, args: dict[str, Any]) -> Any:
    return ToolRegistry([EscalateToHumanTool(context)]).dispatch("escalate_to_human", args)


def log_lines(context: ToolContext) -> list[dict[str, Any]]:
    path: Path = context.escalation_log
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


COMPLAINT = {"reason": "complaint", "summary": "Violão chegou com o tampo rachado."}


def test_creates_protocol_with_date_and_sequence(make_context: Callable[..., ToolContext]) -> None:
    context = make_context(SUNDAY)
    assert escalate(context, COMPLAINT).payload["protocol"] == "EM-20261101-0001"
    assert escalate(context, COMPLAINT).payload["protocol"] == "EM-20261101-0002"


def test_sequence_restarts_each_day(make_context: Callable[..., ToolContext]) -> None:
    escalate(make_context(SUNDAY), COMPLAINT)
    assert escalate(make_context(TUESDAY_MORNING), COMPLAINT).payload["protocol"] == (
        "EM-20261103-0001"
    )


def test_appends_one_json_line_per_call(make_context: Callable[..., ToolContext]) -> None:
    context = make_context(SUNDAY)
    for _ in range(3):
        escalate(context, {**COMPLAINT, "order_id": 8})
    lines = log_lines(context)
    assert len(lines) == 3
    assert set(lines[0]) == {"protocol", "created_at", "reason", "summary", "order_id"}
    assert lines[0]["order_id"] == 8


def test_summary_contacts_are_masked_in_log(make_context: Callable[..., ToolContext]) -> None:
    context = make_context(SUNDAY)
    summary = "Cliente anacarol.ferreira@coldmail.com, telefone (67) 99654-7821, pedido atrasado."
    escalate(context, {"reason": "delayed_order", "summary": summary, "order_id": 8})
    logged = log_lines(context)[0]["summary"]
    assert "a***@coldmail.com" in logged
    assert "(67) *****-7821" in logged
    assert "anacarol" not in logged


def test_closed_store_returns_next_opening(make_context: Callable[..., ToolContext]) -> None:
    payload = escalate(make_context(SATURDAY_AFTERNOON), COMPLAINT).payload
    assert payload["store_open_now"] is False
    assert payload["next_opening"] == "segunda-feira, 09/11/2026, às 09:00"
    assert "closed" in payload["note"].lower()


def test_open_store_has_no_next_opening(make_context: Callable[..., ToolContext]) -> None:
    payload = escalate(make_context(TUESDAY_MORNING), COMPLAINT).payload
    assert payload["store_open_now"] is True
    assert payload["next_opening"] is None


def test_contacts_use_canonical_phone_and_email(make_context: Callable[..., ToolContext]) -> None:
    contacts = escalate(make_context(), COMPLAINT).payload["contacts"]
    assert contacts["phone_whatsapp"] == "(67) 3341-4444"
    assert contacts["email"] == "contato@emporiodamusica.com.br"
    assert "Rua 14 de Maio, 3200" in contacts["address"]


def test_response_time_is_24_business_hours(make_context: Callable[..., ToolContext]) -> None:
    assert escalate(make_context(), COMPLAINT).payload["response_time"] == "até 24 horas úteis"


def test_invalid_reason_rejected(make_context: Callable[..., ToolContext]) -> None:
    result = escalate(make_context(), {"reason": "refund", "summary": "quero reembolso"})
    assert result.payload["error"] == "invalid_arguments"


def test_data_privacy_reason_accepted(make_context: Callable[..., ToolContext]) -> None:
    result = escalate(
        make_context(), {"reason": "data_privacy_request", "summary": "Quer excluir os dados."}
    )
    assert result.ok and result.payload["protocol"].startswith("EM-")
