"""Load the store CSV files once and answer read-only queries (D-05, NFR-01).

There is deliberately no way to list or search customers: an order is looked up by
number and its owner is checked by contact (NFR-03, D-06).
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from rapidfuzz import fuzz

from emporio.models import (
    Category,
    Customer,
    Order,
    OrderItem,
    Product,
    Promotion,
    normalize_text,
)
from emporio.money import Money

CSV_FILES: dict[str, str] = {
    "categories": "desafio_tecnico_ai_eng - categories.csv",
    "products": "desafio_tecnico_ai_eng - products.csv",
    "customers": "desafio_tecnico_ai_eng - customers.csv",
    "orders": "desafio_tecnico_ai_eng - orders.csv",
    "order_items": "desafio_tecnico_ai_eng - order_items.csv",
    "promotions": "desafio_tecnico_ai_eng - promotions.csv",
}

# Instrument words customers use, mapped to category ids.
CATEGORY_ALIASES: dict[str, int] = {
    **dict.fromkeys(["guitarra", "guitarras"], 1),
    **dict.fromkeys(["baixo", "baixos", "contrabaixo", "contrabaixos"], 2),
    **dict.fromkeys(["bateria", "baterias", "percussao"], 3),
    **dict.fromkeys(["teclado", "teclados", "piano", "pianos", "sintetizador"], 4),
    **dict.fromkeys(["violao", "violoes"], 5),
    **dict.fromkeys(["sopro", "saxofone", "saxofones", "sax", "flauta", "flautas", "clarinete"], 6),
    **dict.fromkeys(["metais", "trompete", "trompetes", "trombone", "trombones", "tuba"], 7),
    **dict.fromkeys(["violino", "violinos", "violoncelo", "cello", "orquestrais"], 8),
    **dict.fromkeys(["ukulele", "ukuleles"], 9),
}


class DataLoadError(Exception):
    """Missing file, missing column or invalid row; the message names the file and the row."""


@dataclass(frozen=True)
class ProductFilter:
    category_id: int | None = None
    min_price: Decimal | None = None  # compared with the current price (promotion applied)
    max_price: Decimal | None = None
    text: str | None = None
    string_material: str | None = None  # "nylon" | "steel"
    electric: bool | None = None
    on_sale_only: bool = False
    include_unavailable: bool = False


@dataclass(frozen=True)
class NameMatch:
    product: Product
    score: float  # 0-100


def search_key(text: str) -> str:
    """Normalize for matching: 'Ohana CK-20' -> 'ohana ck20'."""
    flat = normalize_text(text).replace("-", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", flat).split())


# Words that only describe the category; they lower the fuzzy score of the product actually
# named ("violão Giannini GF-3D"), so the name lookup drops them (normalized, no accents).
CATEGORY_WORDS = frozenset(
    {"violao", "guitarra", "baixo", "ukulele", "teclado", "bateria", "instrumento"}
)
_MODEL_CODE = re.compile(r"(?=.*[a-z])(?=.*\d)[a-z0-9]+")


def _without_category_words(query: str) -> str:
    """'violão Giannini GF-3D' -> 'giannini gf3d'; a query made only of them is kept."""
    key = search_key(query)
    kept = [word for word in key.split() if word not in CATEGORY_WORDS]
    return " ".join(kept) if kept else key


def _model_codes(key: str) -> set[str]:
    """Tokens with letters and digits ('gf3d', 'gd20', 'c40')."""
    return {word for word in key.split() if _MODEL_CODE.fullmatch(word)}


def _name_score(query: str, name: str) -> float:
    a, b = search_key(query), search_key(name)
    return max(
        fuzz.token_set_ratio(a, b),
        fuzz.partial_ratio(a.replace(" ", ""), b.replace(" ", "")),
    )


def _optional(value: str) -> str | None:
    value = value.strip()
    return value or None


def _parse_specs(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    return {str(k): str(v) for k, v in json.loads(raw).items()}


def _string_fields(specs: dict[str, str], name: str) -> tuple[str | None, int | None]:
    """Split the mixed 'strings' spec into material and count (A-21)."""
    raw = specs.get("strings")
    seven = re.search(r"(\d+)\s+cordas", normalize_text(name))
    named_count = int(seven.group(1)) if seven else None
    if raw is None:
        return None, named_count
    if raw.isdigit():
        return None, int(raw)
    return raw.lower(), named_count


def _product_row(row: dict[str, str]) -> dict[str, Any]:
    specs = _parse_specs(row["specs"])
    material, count = _string_fields(specs, row["name"])
    return {
        "id": row["product_id"],
        "name": row["name"],
        "category_id": row["category_id"],
        "price": row["price_brl"],
        "description": row["description"],
        "stock_quantity": row["stock_quantity"],
        "status": row["status"],
        "specs": specs,
        "string_material": material,
        "string_count": count,
        "created_at": date.fromisoformat(row["created_at"]),
    }


def _read_csv[ModelT: BaseModel](
    data_dir: Path,
    key: str,
    model: type[ModelT],
    convert: Callable[[dict[str, str]], dict[str, Any]],
) -> list[ModelT]:
    path = data_dir / CSV_FILES[key]
    if not path.is_file():
        raise DataLoadError(f"Missing data file for {key}: {path}")
    items: list[ModelT] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            try:
                items.append(model.model_validate(convert(row)))
            except (KeyError, ValueError, ValidationError, json.JSONDecodeError) as exc:
                raise DataLoadError(f"{path.name}: row {line}: {exc}") from exc
    return items


class StoreData:
    """In-memory, read-only view of the store data."""

    def __init__(
        self,
        categories: list[Category],
        products: list[Product],
        promotions: list[Promotion],
        customers: list[Customer],
        orders: list[Order],
        order_items: list[OrderItem],
    ) -> None:
        self._categories = {c.id: c for c in categories}
        self._products = {p.id: p for p in products}
        self._customers = {c.id: c for c in customers}
        self._orders = {o.id: o for o in orders}
        self._promotions: dict[int, list[Promotion]] = {}
        for promotion in promotions:
            if promotion.product_id not in self._products:
                raise DataLoadError(f"Promotion {promotion.id} points to unknown product")
            self._promotions.setdefault(promotion.product_id, []).append(promotion)
        self._items: dict[int, list[OrderItem]] = {}
        for item in order_items:
            self._items.setdefault(item.order_id, []).append(item)

    @classmethod
    def load(cls, data_dir: Path) -> StoreData:
        return cls(
            categories=_read_csv(
                data_dir,
                "categories",
                Category,
                lambda r: {
                    "id": r["category_id"],
                    "name": r["name"],
                    "description": r["description"],
                },
            ),
            products=_read_csv(data_dir, "products", Product, _product_row),
            promotions=_read_csv(
                data_dir,
                "promotions",
                Promotion,
                lambda r: {
                    "id": r["promotion_id"],
                    "product_id": r["product_id"],
                    "discount_percent": r["discount_percent"],
                    "description": r["description"],
                    "is_active": r["is_active"].strip() == "1",
                },
            ),
            customers=_read_csv(
                data_dir,
                "customers",
                Customer,
                lambda r: {
                    "id": r["customer_id"],
                    "name": r["name"],
                    "phone": r["phone"],
                    "email": r["email"],
                    "city": r["city"],
                },
            ),
            orders=_read_csv(
                data_dir,
                "orders",
                Order,
                lambda r: {
                    "id": r["order_id"],
                    "customer_id": r["customer_id"],
                    "order_date": r["order_date"],
                    "status": r["status"],
                    "total": r["total_brl"],
                    "payment_method": r["payment_method"],
                    "tracking_code": _optional(r["tracking_code"]),
                    "estimated_delivery": _optional(r["estimated_delivery"]),
                    "notes": _optional(r["notes"]),
                },
            ),
            order_items=_read_csv(
                data_dir,
                "order_items",
                OrderItem,
                lambda r: {
                    "order_id": r["order_id"],
                    "product_id": r["product_id"],
                    "quantity": r["quantity"],
                },
            ),
        )

    # ---------- categories ----------

    def categories(self) -> list[Category]:
        return list(self._categories.values())

    def get_category(self, category_id: int) -> Category | None:
        return self._categories.get(category_id)

    def find_category(self, name_or_id: str | int) -> Category | None:
        """Accepts 'violoes', 'Violões', 'violão', 'saxofone' or 5."""
        if isinstance(name_or_id, int) or str(name_or_id).strip().isdigit():
            return self._categories.get(int(name_or_id))
        query = normalize_text(str(name_or_id))
        for category in self._categories.values():
            if normalize_text(category.name) == query:
                return category
        for word in [query, *query.split()]:
            if word in CATEGORY_ALIASES:
                return self._categories.get(CATEGORY_ALIASES[word])
        return None

    # ---------- products ----------

    def products(self) -> list[Product]:
        return list(self._products.values())

    def get_product(self, product_id: int) -> Product | None:
        return self._products.get(product_id)

    def products_in_category(self, category_id: int) -> list[Product]:
        return [p for p in self._products.values() if p.category_id == category_id]

    def active_promotions(self) -> list[Promotion]:
        return [p for items in self._promotions.values() for p in items if p.is_active]

    def promotions_for(self, product_id: int) -> list[Promotion]:
        return list(self._promotions.get(product_id, []))

    def active_promotion_for(self, product_id: int) -> Promotion | None:
        """Only is_active = 1 counts; with two active ones, the larger discount (A-13)."""
        active = [p for p in self._promotions.get(product_id, []) if p.is_active]
        return max(active, key=lambda p: p.discount_percent, default=None)

    def current_price(self, product: Product) -> Money:
        promotion = self.active_promotion_for(product.id)
        if promotion is None:
            return product.price
        return product.price.apply_discount(promotion.discount_percent)

    def _is_electric(self, product: Product) -> bool:
        name = normalize_text(product.name)
        return product.specs.get("electronics") == "yes" or "eletric" in name or "electric" in name

    def search_products(self, flt: ProductFilter) -> list[Product]:
        """Sorted by current price, then name."""
        # Every word must appear as a whole word; words that only name the category are dropped
        words = _without_category_words(flt.text).split() if flt.text else []
        results: list[tuple[Money, str, Product]] = []
        for product in self._products.values():
            if not flt.include_unavailable and not product.is_sellable:
                continue
            if flt.category_id is not None and product.category_id != flt.category_id:
                continue
            price = self.current_price(product)
            if flt.min_price is not None and price < Money(flt.min_price):
                continue
            if flt.max_price is not None and Money(flt.max_price) < price:
                continue
            if flt.on_sale_only and self.active_promotion_for(product.id) is None:
                continue
            if flt.string_material and product.string_material != flt.string_material:
                continue
            if flt.electric is not None and self._is_electric(product) != flt.electric:
                continue
            # Whole words only: "kala" must not match "Kalani"
            haystack = set(search_key(f"{product.name} {product.description}").split())
            if any(word not in haystack for word in words):
                continue
            results.append((price, product.name, product))
        results.sort(key=lambda r: (r[0].amount, r[1]))
        return [r[2] for r in results]

    def match_product_name(self, query: str, limit: int = 3) -> list[NameMatch]:
        """Fuzzy match on product names, best first (D-13).

        Category words are dropped first; a model code found in exactly one product name
        makes that product the only match.
        """
        query = _without_category_words(query)
        codes = _model_codes(query)
        if codes:
            owners = [p for p in self._products.values() if codes & set(search_key(p.name).split())]
            if len(owners) == 1:
                return [NameMatch(owners[0], 100.0)]
        # Query words that are whole words of some product name ("kala") must be whole words of
        # the match too, so "Kala" does not bring "Kalani"; other words are left to fuzzy matching
        names = {p.id: set(search_key(p.name).split()) for p in self._products.values()}
        known = {word for word in query.split() if any(word in n for n in names.values())}
        pool = [p for p in self._products.values() if known <= names[p.id]]
        scored = [NameMatch(p, _name_score(query, p.name)) for p in pool or self._products.values()]
        scored.sort(key=lambda m: (-m.score, m.product.id))
        return scored[:limit]

    # ---------- orders and customers ----------

    def get_order(self, order_id: int) -> Order | None:
        return self._orders.get(order_id)

    def order_items(self, order_id: int) -> list[OrderItem]:
        return list(self._items.get(order_id, []))

    def order_count(self) -> int:
        return len(self._orders)

    def get_customer(self, customer_id: int) -> Customer | None:
        """By id only; there is no listing or search of customers (NFR-03)."""
        return self._customers.get(customer_id)

    def customer_count(self) -> int:
        return len(self._customers)
