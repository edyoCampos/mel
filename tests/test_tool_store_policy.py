"""get_store_policy tool (FR-07, FR-08, FR-10, D-02, D-21)."""

from collections.abc import Callable
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
