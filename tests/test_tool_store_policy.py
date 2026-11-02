"""get_store_policy tool (FR-07, FR-08, FR-10, D-02, D-21)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from emporio.policies import PolicyTopic
from emporio.tools import GetStorePolicyTool, ToolContext, ToolRegistry


@pytest.fixture
def policy(make_context: Callable[..., ToolContext]) -> Callable[[dict[str, Any]], Any]:
    registry = ToolRegistry([GetStorePolicyTool(make_context())])
    return lambda args: registry.dispatch("get_store_policy", args)


def test_contact_returns_address(policy: Any) -> None:
    payload = policy({"topic": "contact"}).payload
    assert payload["topic"] == "contact"
    assert payload["manual_sections"] == ["1.2"]
    assert payload["manual_version"] == "2.1 (June 2025)"
    assert "Rua 14 de Maio, 3200" in payload["text"]


def test_payment_returns_combination_rule(policy: Any) -> None:
    assert "R$ 2.000,00" in policy({"topic": "payment"}).payload["text"]


def test_shipping_returns_special_quote_rule(policy: Any) -> None:
    assert "cotação individual" in policy({"topic": "shipping"}).payload["text"]


def test_returns_has_regret_conditions(policy: Any) -> None:
    text = policy({"topic": "returns"}).payload["text"]
    assert "7 (sete) dias" in text
    assert "10 dias úteis" in text


def test_scope_has_accessories_rule(policy: Any) -> None:
    assert "não comercializamos acessórios" in policy({"topic": "scope"}).payload["text"]


def test_invalid_topic_rejected(policy: Any) -> None:
    assert policy({"topic": "discounts"}).payload["error"] == "invalid_arguments"


@pytest.mark.parametrize("topic", [t.value for t in PolicyTopic])
def test_every_topic_maps_to_sections(policy: Any, topic: str) -> None:
    payload = policy({"topic": topic}).payload
    assert payload["manual_sections"]
    assert payload["text"]


# ---------- Seasonal campaigns computed from promotions.csv (FR-15, AS-79) ----------


def test_promotions_topic_lists_no_active_campaign(policy: Any) -> None:
    payload = policy({"topic": "promotions"}).payload
    assert payload["active_campaign_promotions"] == []  # all 4 active rows are not campaigns
    assert "Black Friday" in payload["text"]  # the manual ranges stay visible (§7.3)
    assert "redes sociais" in payload["note"]


def test_active_campaign_is_listed(tmp_path: Any) -> None:
    from datetime import datetime

    from emporio.models import Category
    from emporio.policies import PolicyBook
    from emporio.services import STORE_TZ
    from emporio.store_data import StoreData
    from tests.factories import make_product, make_promotion

    product = make_product(id=91, name="Violão Teste", category_id=1, price="1000")
    data = StoreData(
        categories=[Category(id=1, name="Violões", description="")],
        products=[product],
        promotions=[
            make_promotion(
                product, discount_percent=30, is_active=True, id=7, description="Black Friday"
            ),
            make_promotion(
                product, discount_percent=10, is_active=True, id=8, description="Oferta Especial"
            ),
        ],
        customers=[],
        orders=[],
        order_items=[],
    )
    context = ToolContext(
        data=data,
        policies=PolicyBook.load(Path("data/policies.md")),
        now=lambda: datetime(2026, 11, 27, 10, tzinfo=STORE_TZ),
        escalation_log=tmp_path / "esc.jsonl",
    )
    payload = (
        ToolRegistry([GetStorePolicyTool(context)])
        .dispatch("get_store_policy", {"topic": "promotions"})
        .payload
    )
    assert payload["active_campaign_promotions"] == [
        {"campaign": "Black Friday", "product": "Violão Teste", "discount_percent": 30}
    ]


def test_other_topics_have_no_campaign_field(policy: Any) -> None:
    assert "active_campaign_promotions" not in policy({"topic": "contact"}).payload
