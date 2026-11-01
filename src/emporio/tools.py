"""Agent tools: typed commands, JSON schemas for the LLM and dispatch by name (D-03, D-21).

Tools only read data. The single write in the package is the escalation log line (NFR-01).
Money is always returned as formatted text, e.g. "R$ 2.199,00" (NFR-04).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from emporio.models import Product
from emporio.policies import PolicyBook
from emporio.services import PricingService, ShippingRules
from emporio.store_data import ProductFilter, StoreData

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    data: StoreData
    policies: PolicyBook
    now: Callable[[], datetime]  # injected clock (D-07)
    escalation_log: Path  # e.g. logs/escalations.jsonl (git-ignored)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


def _error(code: str, message: str) -> ToolResult:
    return ToolResult(ok=False, payload={"error": code, "message": message})


class ToolError(Exception):
    """Raised by a tool to return a known error code to the model."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _clean_schema(node: Any) -> Any:
    """Make Pydantic JSON schemas friendly to OpenAI-compatible providers.

    Removes titles and null defaults, and turns `X | None` (anyOf with null) into X.
    """
    if isinstance(node, list):
        return [_clean_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" or (key == "default" and value is None):
            continue
        cleaned[key] = _clean_schema(value)
    variants = cleaned.get("anyOf")
    if isinstance(variants, list):
        non_null = [v for v in variants if v != {"type": "null"}]
        if len(non_null) == 1:
            del cleaned["anyOf"]
            cleaned = {**non_null[0], **cleaned}
    return cleaned


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]  # tells the model WHEN to use the tool
    args_model: ClassVar[type[BaseModel]]

    def __init__(self, context: ToolContext) -> None:
        self.context = context

    @abstractmethod
    def run(self, args: BaseModel) -> dict[str, Any]: ...

    @classmethod
    def schema(cls) -> dict[str, Any]:
        parameters = _clean_schema(cls.args_model.model_json_schema())
        parameters.setdefault("additionalProperties", False)
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": parameters,
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def dispatch(self, name: str, raw_arguments: str | dict[str, Any]) -> ToolResult:
        """Never raises for model mistakes; errors go back to the model as results."""
        tool = self._tools.get(name)
        if tool is None:
            return _error("unknown_tool", f"No tool named {name!r}. Available: {self.names()}")
        try:
            arguments = (
                json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            )
        except json.JSONDecodeError as exc:
            return _error("invalid_arguments", f"Arguments are not valid JSON: {exc.msg}")
        if not isinstance(arguments, dict):
            return _error("invalid_arguments", "Arguments must be a JSON object")
        try:
            args = tool.args_model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
                for err in exc.errors()
            )
            return _error("invalid_arguments", problems)
        try:
            return ToolResult(ok=True, payload=tool.run(args))
        except ToolError as exc:
            return _error(exc.code, exc.message)
        except Exception:
            logger.exception("Tool %s failed", name)
            return _error("internal_error", "The tool failed unexpectedly. Offer human help.")


# ---------- shared product formatting ----------

_pricing = PricingService()


def _category_name(data: StoreData, product: Product) -> str:
    category = data.get_category(product.category_id)
    return category.name if category else ""


def product_summary(data: StoreData, product: Product) -> dict[str, Any]:
    quote = _pricing.quote(product, data.promotions_for(product.id))
    promotion = None
    if quote.active_promotion is not None and quote.promotional_price is not None:
        promotion = {
            "discount_percent": quote.active_promotion.discount_percent,
            "promotional_price": str(quote.promotional_price),
            "description": quote.active_promotion.description,
        }
    return {
        "id": product.id,
        "name": product.name,
        "category": _category_name(data, product),
        "availability": product.availability.value,
        "price": str(quote.list_price),
        "current_price": str(quote.effective_price),
        "promotion": promotion,
        "pix_price": str(quote.pix_price) if quote.pix_price is not None else None,
        "best_installments": quote.best_installments.describe_pt(),
    }


# ---------- search_products (FR-02, FR-16) ----------

SEARCH_LIMIT = 15  # AS-06: FR-02 must list the 12 violoes up to R$ 1.000


class SearchProductsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(
        default=None,
        description="Category as the customer said it, e.g. 'violões', 'ukulele', 'guitarra'.",
    )
    min_price: float | None = Field(
        default=None, ge=0, description="BRL, compared with the current price."
    )
    max_price: float | None = Field(
        default=None, ge=0, description="BRL, compared with the current price."
    )
    text: str | None = Field(
        default=None, description="Words to match in name or description, e.g. 'Yamaha'."
    )
    string_material: Literal["nylon", "steel"] | None = Field(
        default=None, description="Acoustic guitars and ukuleles only."
    )
    electric: bool | None = Field(default=None, description="Has a pickup / electronics.")
    on_sale_only: bool = Field(default=False, description="Only items with an active promotion.")
    include_unavailable: bool = Field(
        default=False,
        description="Also list out-of-stock, discontinued and coming-soon items, marked as such.",
    )


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class SearchProductsTool(Tool):
    name = "search_products"
    description = (
        "Search the store catalog. Use it for questions about which instruments are "
        "available, in a category or price range. Never state prices or availability "
        "without calling a tool."
    )
    args_model = SearchProductsArgs

    def run(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, SearchProductsArgs)
        data = self.context.data
        category = None
        if args.category:
            category = data.find_category(args.category)
            if category is None:
                known = ", ".join(c.name for c in data.categories())
                return {
                    "category": None,
                    "total_matches": 0,
                    "returned": 0,
                    "products": [],
                    "note": f"Unknown category. Known categories: {known}.",
                }
        matches = data.search_products(
            ProductFilter(
                category_id=category.id if category else None,
                min_price=_decimal(args.min_price),
                max_price=_decimal(args.max_price),
                text=args.text,
                string_material=args.string_material,
                electric=args.electric,
                on_sale_only=args.on_sale_only,
                include_unavailable=args.include_unavailable,
            )
        )
        note = None
        if not matches and category is not None and not data.products_in_category(category.id):
            note = "No products registered in this category."
        elif len(matches) > SEARCH_LIMIT:
            note = f"Showing the {SEARCH_LIMIT} cheapest of {len(matches)} matches."
        shown = matches[:SEARCH_LIMIT]
        return {
            "category": category.name if category else None,
            "total_matches": len(matches),
            "returned": len(shown),
            "products": [product_summary(data, p) for p in shown],
            "note": note,
        }


# ---------- get_product_details (FR-03, FR-04, FR-05, FR-07c) ----------

MATCH_SCORE = 85  # AS-24
MATCH_MARGIN = 5
CANDIDATE_SCORE = 60
ALTERNATIVES_LIMIT = 3
ALTERNATIVES_PRICE_RANGE = Decimal("0.30")  # FR-04: +/- 30%

_shipping = ShippingRules()


class GetProductDetailsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int | None = Field(default=None, ge=1, description="Product id, if known.")
    query: str | None = Field(
        default=None,
        min_length=2,
        description="Product name as the customer wrote it; typos are fine.",
    )

    @model_validator(mode="after")
    def _one_of(self) -> GetProductDetailsArgs:
        if self.product_id is None and not self.query:
            raise ValueError("Give product_id or query")
        return self


class GetProductDetailsTool(Tool):
    name = "get_product_details"
    description = (
        "Get price, PIX price, installments, promotion, availability, local delivery fee and "
        "similar alternatives for ONE product. Use it whenever the customer asks about a "
        "specific instrument. Copy amounts exactly as returned. Pass product_id or query."
    )
    args_model = GetProductDetailsArgs

    def run(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, GetProductDetailsArgs)
        data = self.context.data
        if args.product_id is not None:
            product = data.get_product(args.product_id)
            if product is None:
                raise ToolError("not_found", f"No product with id {args.product_id}.")
            return self._details(product)
        assert args.query is not None
        matches = data.match_product_name(args.query, limit=ALTERNATIVES_LIMIT)
        best = matches[0] if matches else None
        second = matches[1].score if len(matches) > 1 else 0.0
        if best and best.score >= MATCH_SCORE and best.score - second >= MATCH_MARGIN:
            return self._details(best.product)
        candidates = [m for m in matches if m.score >= CANDIDATE_SCORE]
        if candidates:
            return {
                "status": "ambiguous",
                "candidates": [
                    {
                        "id": m.product.id,
                        "name": m.product.name,
                        "current_price": str(data.current_price(m.product)),
                        "availability": m.product.availability.value,
                    }
                    for m in candidates
                ],
                "note": "Ask the customer which one they mean.",
            }
        return {"status": "not_found", "message": f"No product matches {args.query!r}."}

    def _details(self, product: Product) -> dict[str, Any]:
        data = self.context.data
        summary = product_summary(data, product)
        inactive = [
            {"discount_percent": p.discount_percent, "description": p.description}
            for p in data.promotions_for(product.id)
            if not p.is_active
        ]
        promotion_display = None
        if summary["promotion"] is not None:
            promotion = summary["promotion"]
            promotion_display = (
                f"de {summary['price']} por {promotion['promotional_price']} "
                f"({promotion['discount_percent']}% off)"
            )
        notes = []
        if summary["promotion"] is not None:
            notes.append("Promotions do not stack with the PIX discount.")
            notes.append(
                "Items sold out during a promotion do not keep the promotional price "
                "(no rain check)."
            )
        if inactive:
            notes.append(
                "inactive_promotions are no longer valid: say so and give the current price."
            )
        return {
            "status": "found",
            "product": {
                "id": product.id,
                "name": product.name,
                "category": summary["category"],
                "description": product.description,
                "specs": product.specs,
                "availability": summary["availability"],
            },
            "pricing": {
                "list_price": summary["price"],
                "active_promotion": summary["promotion"],
                "inactive_promotions": inactive,
                "current_price": summary["current_price"],
                "pix_price": summary["pix_price"],
                "best_installments": summary["best_installments"],
                "promotion_display": promotion_display,
            },
            "delivery": self._delivery(product),
            "alternatives": self._alternatives(product),
            "notes": notes,
        }

    def _delivery(self, product: Product) -> dict[str, Any]:
        if _shipping.requires_special_quote(product):
            quote = "cotação especial — encaminhar para um atendente (escalate_to_human)"
            return {
                "campo_grande_fee": quote,
                "campo_grande_time": "a combinar",
                "other_cities": quote,
                "requires_special_quote": True,
            }
        fee = _shipping.local_delivery_fee(self.context.data.current_price(product))
        return {
            "campo_grande_fee": "Grátis" if fee == fee.zero() else str(fee),
            "campo_grande_time": "1 a 3 dias úteis",
            "other_cities": (
                "Correios (PAC ou SEDEX) ou Jadlog; valor calculado pelo CEP — "
                "encaminhar para cotação"
            ),
            "requires_special_quote": False,
        }

    def _alternatives(self, product: Product) -> list[dict[str, Any]]:
        if product.is_sellable:
            return []
        data = self.context.data
        base = data.current_price(product).amount
        limit = base * ALTERNATIVES_PRICE_RANGE
        options = []
        for other in data.products_in_category(product.category_id):
            if other.id == product.id or not other.is_sellable:
                continue
            price = data.current_price(other).amount
            if abs(price - base) <= limit:
                options.append((abs(price - base), price, other))
        options.sort(key=lambda o: (o[0], o[1]))
        return [
            {
                "id": other.id,
                "name": other.name,
                "current_price": str(data.current_price(other)),
                "availability": other.availability.value,
            }
            for _, _, other in options[:ALTERNATIVES_LIMIT]
        ]
