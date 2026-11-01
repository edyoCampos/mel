"""get_product_details tool (FR-03, FR-04, FR-05, FR-07c, R-17, A-19, A-25, D-13)."""

from collections.abc import Callable
from typing import Any

import pytest

from emporio.store_data import StoreData
from emporio.tools import GetProductDetailsTool, ToolContext, ToolRegistry
from tests.conftest import SUNDAY_1_NOV
from tests.factories import make_product


@pytest.fixture
def details(make_context: Callable[..., ToolContext]) -> Callable[[dict[str, Any]], Any]:
    registry = ToolRegistry([GetProductDetailsTool(make_context())])
    return lambda args: registry.dispatch("get_product_details", args)


def alt_ids(result: Any) -> list[int]:
    return [a["id"] for a in result.payload["alternatives"]]


def test_takamine_by_query_with_typo(details: Any) -> None:
    result = details({"query": "takamine gd 20"})
    assert result.ok and result.payload["status"] == "found"
    assert result.payload["product"]["id"] == 95
    pricing = result.payload["pricing"]
    assert pricing["list_price"] == "R$ 2.199,00"
    assert pricing["current_price"] == "R$ 2.199,00"
    assert pricing["pix_price"] == "R$ 2.089,05"
    assert pricing["best_installments"] == "12x de R$ 183,25 sem juros"


def test_takamine_inactive_promotion_listed_without_price(details: Any) -> None:
    result = details({"product_id": 95})
    pricing = result.payload["pricing"]
    assert pricing["active_promotion"] is None
    assert pricing["inactive_promotions"] == [
        {"discount_percent": 12, "description": "Oferta Especial"}
    ]
    assert "R$ 1.935,12" not in result.to_json()


def test_ohana_promotion_display_and_no_pix(details: Any) -> None:
    pricing = details({"product_id": 121}).payload["pricing"]
    assert pricing["promotion_display"] == "de R$ 549,00 por R$ 439,20 (20% off)"
    assert pricing["current_price"] == "R$ 439,20"
    assert pricing["pix_price"] is None
    assert pricing["best_installments"] == "5x de R$ 87,84 sem juros"


def test_out_of_stock_gf3d_alternatives(details: Any) -> None:
    result = details({"query": "giannini gf-3d"})
    assert result.payload["product"]["availability"] == "out_of_stock"
    assert alt_ids(result) == [107, 82, 86]
    assert result.payload["alternatives"][0]["current_price"] == "R$ 799,00"


def test_discontinued_sn7c_alternatives(details: Any) -> None:
    result = details({"product_id": 113})
    assert result.payload["product"]["availability"] == "discontinued"
    assert alt_ids(result) == [86, 87, 107]


def test_coming_soon_kala_alternatives(details: Any) -> None:
    result = details({"product_id": 130})
    assert result.payload["product"]["availability"] == "coming_soon"
    assert alt_ids(result) == [126, 129, 125]


def test_discontinued_with_stock_is_not_available(details: Any) -> None:
    result = details({"product_id": 136})
    assert result.payload["product"]["availability"] == "discontinued"
    assert result.payload["alternatives"] == []


def test_available_product_has_no_alternatives(details: Any) -> None:
    assert details({"product_id": 95}).payload["alternatives"] == []


def test_drum_kit_requires_special_quote(details: Any) -> None:
    delivery = details({"query": "bateria pearl"}).payload["delivery"]
    assert delivery["requires_special_quote"] is True
    assert "cotação especial" in delivery["campo_grande_fee"]


def test_electric_bass_has_normal_delivery(details: Any) -> None:
    delivery = details({"product_id": 135}).payload["delivery"]
    assert delivery["requires_special_quote"] is False
    assert delivery["campo_grande_fee"] == "Grátis"


def _context_with_price(price: str, store: StoreData, base: ToolContext) -> ToolContext:
    product = make_product(id=1, price=price)
    data = StoreData(store.categories(), [product], [], [], [], [])
    return ToolContext(data, base.policies, lambda: SUNDAY_1_NOV, base.escalation_log)


def test_local_fee_threshold(
    make_context: Callable[..., ToolContext], store_data: StoreData
) -> None:
    for price, fee in [("500", "R$ 35,00"), ("500.01", "Grátis")]:
        tool = GetProductDetailsTool(_context_with_price(price, store_data, make_context()))
        payload = ToolRegistry([tool]).dispatch("get_product_details", {"product_id": 1}).payload
        assert payload["delivery"]["campo_grande_fee"] == fee


def test_ambiguous_query_returns_candidates(details: Any) -> None:
    result = details({"query": "takamine"})
    assert result.payload["status"] == "ambiguous"
    candidates = result.payload["candidates"]
    assert 1 < len(candidates) <= 3
    assert all("Takamine" in c["name"] for c in candidates)


def test_unknown_query_not_found(details: Any) -> None:
    result = details({"query": "violino stradivarius"})
    assert result.payload["status"] == "not_found"


def test_unknown_product_id_error(details: Any) -> None:
    result = details({"product_id": 9999})
    assert not result.ok
    assert result.payload["error"] == "not_found"


def test_requires_id_or_query(details: Any) -> None:
    assert details({}).payload["error"] == "invalid_arguments"
