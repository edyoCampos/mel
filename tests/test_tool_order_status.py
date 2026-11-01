"""get_order_status tool (FR-06, FR-08, R-11, R-13, A-07, A-16, A-20, NFR-03)."""

import csv
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest

from emporio.services import STORE_TZ
from emporio.tools import GetOrderStatusTool, ToolContext, ToolRegistry
from tests.conftest import ROOT

FEB_20 = datetime(2026, 2, 20, 10, 0, tzinfo=STORE_TZ)


@pytest.fixture
def order_tool(make_context: Callable[..., ToolContext]) -> Callable[..., Any]:
    def call(args: dict[str, Any], now: datetime | None = None) -> Any:
        context = make_context(now) if now else make_context()
        return ToolRegistry([GetOrderStatusTool(context)]).dispatch("get_order_status", args)

    return call


def test_requires_contact(order_tool: Any) -> None:
    assert order_tool({"order_id": 8}).payload["error"] == "invalid_arguments"


def test_verified_with_email_shipped_and_delayed(order_tool: Any) -> None:
    result = order_tool({"order_id": 8, "email": "anacarol.ferreira@coldmail.com"})
    assert result.ok
    payload = result.payload
    assert payload["verified"] is True
    order = payload["order"]
    assert order["status"] == "shipped"
    assert order["status_label"] == "enviado"
    assert order["tracking_code"] == "BRJL5544332BR"
    assert order["estimated_delivery"] == "28/02/2026"
    assert order["total"] == "R$ 349,90"
    assert order["payment_method"] == "cartão de débito"
    assert order["items"] == [{"name": "Kala KA-C Concert Mogno", "quantity": 1}]
    assert payload["delay"] == {"is_delayed": True, "reason": "estimated_delivery_passed"}
    assert payload["return_windows"] is None
    assert any("escalate_to_human" in note for note in payload["notes"])


def test_verified_with_phone_other_format(order_tool: Any) -> None:
    assert order_tool({"order_id": 8, "phone": "67996547821"}).payload["verified"] is True


def test_wrong_contact_reveals_nothing(order_tool: Any) -> None:
    result = order_tool({"order_id": 8, "email": "ana@gmail.com"})
    assert not result.ok
    assert result.payload["error"] == "verification_failed"
    text = result.to_json()
    for secret in ["BRJL", "Ana", "Kala", "28/02"]:
        assert secret not in text


def test_unknown_order_same_error_as_wrong_contact(order_tool: Any) -> None:
    wrong_contact = order_tool({"order_id": 8, "email": "ana@gmail.com"}).payload
    unknown_order = order_tool({"order_id": 999, "email": "ana@gmail.com"}).payload
    assert wrong_contact == unknown_order


def test_first_name_only_after_verification(order_tool: Any) -> None:
    ok = order_tool({"order_id": 8, "email": "anacarol.ferreira@coldmail.com"})
    assert ok.payload["customer_first_name"] == "Ana"
    failed = order_tool({"order_id": 8, "email": "ana@gmail.com"})
    assert "customer_first_name" not in failed.payload


def test_delivered_order_return_windows_open(order_tool: Any) -> None:
    result = order_tool({"order_id": 7, "email": "leticia.rocha@jmail.com"}, now=FEB_20)
    windows = result.payload["return_windows"]
    assert windows["receipt_date_assumed"] == "17/02/2026"
    assert windows["regret"] == {"deadline": "24/02/2026", "is_open": True}
    assert windows["conditions_topic"] == "returns"
    notes = " ".join(result.payload["notes"])
    assert "A-11" in notes and "online" in notes


def test_delivered_order_return_windows_closed_today(order_tool: Any) -> None:
    windows = order_tool({"order_id": 7, "email": "leticia.rocha@jmail.com"}).payload[
        "return_windows"
    ]
    assert not windows["regret"]["is_open"]
    assert not windows["preference_exchange"]["is_open"]
    assert not windows["defect_exchange"]["is_open"]
    assert windows["legal_warranty"] == {"deadline": "18/05/2026", "is_open": False}


def test_cancelled_order_has_reason(order_tool: Any) -> None:
    order = order_tool({"order_id": 18, "email": "gustavo.nunes@inlook.com"}).payload["order"]
    assert order["status_label"] == "cancelado"
    assert order["cancellation_reason"] == "Item fora de estoque no momento do pedido"


def test_registered_total_kept(order_tool: Any) -> None:
    order = order_tool({"order_id": 3, "email": "isabela.moreira@jmail.com"}).payload["order"]
    assert order["total"] == "R$ 3.450,00"


def test_shared_email_verifies_only_own_order(order_tool: Any) -> None:
    assert order_tool({"order_id": 10, "email": "bruno.martins@jmail.com"}).payload["verified"]
    assert not order_tool({"order_id": 8, "email": "bruno.martins@jmail.com"}).ok


def test_confirmed_order_not_shipped_is_delayed(order_tool: Any) -> None:
    payload = order_tool({"order_id": 9, "email": "juliana.almeida@hayoo.com.br"}).payload
    assert payload["delay"] == {"is_delayed": True, "reason": "not_shipped_after_7_days"}


def test_payload_has_no_contacts(order_tool: Any) -> None:
    with (ROOT / "data/raw/desafio_tecnico_ai_eng - customers.csv").open(encoding="utf-8") as f:
        customer = next(c for c in csv.DictReader(f) if c["customer_id"] == "2")
    text = order_tool({"order_id": 8, "phone": customer["phone"]}).to_json()
    assert customer["email"] not in text
    assert customer["phone"] not in text
