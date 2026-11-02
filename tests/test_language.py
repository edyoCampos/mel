"""Customer language: detection and the code-written phrases in Portuguese and English (AS-84)."""

from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import pytest

from emporio.models import OrderStatus, PaymentMethod
from emporio.services import (
    CUSTOMER_LANGUAGE,
    STORE_TZ,
    StoreHours,
    describe_opening,
    detect_language,
)
from emporio.tools import EscalateToHumanTool, GetOrderStatusTool, ToolContext, ToolRegistry

SUNDAY = datetime(2026, 11, 1, 10, 0, tzinfo=STORE_TZ)
TUESDAY_OPENING = datetime(2026, 11, 3, 9, 0, tzinfo=STORE_TZ)


@pytest.fixture
def english() -> Iterator[None]:
    token = CUSTOMER_LANGUAGE.set("en")
    yield
    CUSTOMER_LANGUAGE.reset(token)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hi! Where is my order 8? My email is ana@example.com", "en"),
        ("Yes, please open a ticket for me.", "en"),
        ("Cadê o meu pedido 8?", "pt"),
        ("quanto custa o Music Man Bass 1X Electric Bass?", "pt"),
        ("ok", None),
        ("Takamine GD20", None),
    ],
)
def test_detect_language(text: str, expected: str | None) -> None:
    assert detect_language(text) == expected


def test_default_language_is_portuguese() -> None:
    assert CUSTOMER_LANGUAGE.get() == "pt"


def test_opening_in_both_languages() -> None:
    assert describe_opening(TUESDAY_OPENING) == "terça-feira, 03/11/2026, às 09:00"
    assert describe_opening(TUESDAY_OPENING, "en") == "Tuesday, 03/11/2026, at 09:00"


def test_store_status_line_in_english() -> None:
    line = StoreHours().describe(SUNDAY, "en")
    assert "Next opening: Tuesday, 03/11/2026, at 09:00" in line
    assert "terça" not in line


def test_order_status_and_payment_labels_in_english() -> None:
    assert OrderStatus.SHIPPED.label("en") == "shipped"
    assert OrderStatus.SHIPPED.label("pt") == "enviado"
    assert OrderStatus.CANCELLED.label("en") == "cancelled"
    assert PaymentMethod.parse("credit_12x").label("en") == "credit card in 12x"
    assert PaymentMethod.parse("debit").label("en") == "debit card"
    assert PaymentMethod.parse("pix").label("pt") == "PIX"


def _escalate(context: ToolContext) -> Any:
    args = {"reason": "delayed_order", "summary": "Order 8 is late."}
    return ToolRegistry([EscalateToHumanTool(context)]).dispatch("escalate_to_human", args)


def test_escalation_phrases_in_portuguese_by_default(
    make_context: Callable[..., ToolContext],
) -> None:
    payload = _escalate(make_context(SUNDAY)).payload
    assert payload["response_time"] == "até 24 horas úteis"
    assert payload["next_opening"] == "terça-feira, 03/11/2026, às 09:00"


@pytest.mark.usefixtures("english")
def test_escalation_phrases_in_english(make_context: Callable[..., ToolContext]) -> None:
    payload = _escalate(make_context(SUNDAY)).payload
    assert payload["response_time"] == "within 24 business hours"
    assert payload["next_opening"] == "Tuesday, 03/11/2026, at 09:00"


@pytest.mark.usefixtures("english")
def test_order_labels_in_english(make_context: Callable[..., ToolContext]) -> None:
    registry = ToolRegistry([GetOrderStatusTool(make_context(SUNDAY))])
    args = {"order_id": 8, "email": "anacarol.ferreira@coldmail.com"}
    order = registry.dispatch("get_order_status", args).payload["order"]
    assert order["status_label"] == "shipped"
