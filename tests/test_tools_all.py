"""Properties of the full tool set (tools#1, #8, #9, #10; NFR-01, NFR-03, NFR-04)."""

import csv
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from emporio.store_data import StoreData
from emporio.tools import ToolContext, ToolRegistry
from tests.conftest import ROOT, SUNDAY_1_NOV

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_products", {"category": "violões", "max_price": 1000}),
    ("search_products", {"category": "ukulele", "on_sale_only": True}),
    ("get_product_details", {"query": "takamine gd 20"}),
    ("get_product_details", {"product_id": 96}),
    ("get_product_details", {"product_id": 140}),
    ("get_order_status", {"order_id": 8, "email": "anacarol.ferreira@coldmail.com"}),
    ("get_order_status", {"order_id": 7, "phone": "(67) 99789-0123"}),
    ("get_order_status", {"order_id": 8, "email": "ana@gmail.com"}),
    ("get_store_policy", {"topic": "contact"}),
    (
        "escalate_to_human",
        {
            "reason": "complaint",
            "summary": "Instrumento com defeito.",
            "customer_contact": "Ana Ferreira, (67) 99654-7821",
        },
    ),
]


def walk(node: Any) -> list[Any]:
    if isinstance(node, dict):
        return [v for value in node.values() for v in walk(value)]
    if isinstance(node, list):
        return [v for value in node for v in walk(value)]
    return [node]


def test_registry_exposes_five_tools_in_order(make_context: Callable[..., ToolContext]) -> None:
    assert ToolRegistry.default(make_context()).names() == [
        "search_products",
        "get_product_details",
        "get_order_status",
        "get_store_policy",
        "escalate_to_human",
    ]


def test_every_schema_is_provider_friendly(make_context: Callable[..., ToolContext]) -> None:
    for schema in ToolRegistry.default(make_context()).schemas():
        text = json.dumps(schema)
        assert "$ref" not in text and "anyOf" not in text and '"title"' not in text
        assert schema["function"]["parameters"]["additionalProperties"] is False


def test_results_never_contain_floats(make_context: Callable[..., ToolContext]) -> None:
    registry = ToolRegistry.default(make_context())
    for name, args in CALLS:
        payload = registry.dispatch(name, args).payload
        assert not [v for v in walk(payload) if isinstance(v, float)], name


def test_results_never_contain_customer_contacts(
    make_context: Callable[..., ToolContext],
) -> None:
    with (ROOT / "data/raw/desafio_tecnico_ai_eng - customers.csv").open(encoding="utf-8") as f:
        customers = list(csv.DictReader(f))
    registry = ToolRegistry.default(make_context())
    for name, args in CALLS:
        text = registry.dispatch(name, args).to_json()
        for customer in customers:
            assert customer["email"] not in text, (name, customer["email"])
            assert customer["phone"] not in text, (name, customer["phone"])


def test_only_escalation_writes_to_disk(
    make_context: Callable[..., ToolContext], tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    shutil.copytree(ROOT / "data" / "raw", data_dir)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    base = make_context()
    context = ToolContext(
        data=StoreData.load(data_dir),
        policies=base.policies,
        now=lambda: SUNDAY_1_NOV,
        escalation_log=tmp_path / "logs" / "escalations.jsonl",
    )
    registry = ToolRegistry.default(context)
    for name, args in CALLS:
        if name != "escalate_to_human":
            registry.dispatch(name, args)
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    registry.dispatch(*CALLS[-1])
    written = {p for p in tmp_path.rglob("*") if p.is_file()} - set(before)
    assert written == {tmp_path / "logs" / "escalations.jsonl"}
