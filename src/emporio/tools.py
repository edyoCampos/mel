"""Agent tools: typed commands, JSON schemas for the LLM and dispatch by name (D-03, D-21).

Tools only read data. The single write in the package is the escalation log line (NFR-01).
Money is always returned as formatted text, e.g. "R$ 2.199,00" (NFR-04).
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from emporio.models import Product, normalize_text
from emporio.policies import MANUAL_VERSION, PolicyBook, PolicyTopic
from emporio.services import (
    CUSTOMER_LANGUAGE,
    STORE_TZ,
    CustomerVerifier,
    DelayAssessor,
    PricingService,
    ReturnWindows,
    ShippingRules,
    StoreHours,
    Window,
    describe_opening,
    mask_email,
    mask_phone,
)
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


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace '$ref' with the referenced '$defs' entry; some providers reject references."""
    definitions = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = definitions[node["$ref"].rsplit("/", 1)[-1]]
            extra = {k: v for k, v in node.items() if k != "$ref"}
            return resolve({**target, **extra})
        return {k: resolve(v) for k, v in node.items() if k != "$defs"}

    return resolve(schema)


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
        parameters = _clean_schema(_inline_refs(cls.args_model.model_json_schema()))
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

    @classmethod
    def default(cls, context: ToolContext) -> ToolRegistry:
        """The agent's tools, in a fixed order."""
        return cls(_default_tools(context))

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
        "price_display": quote.price_display,
        "installments_display": quote.installments_display,
    } | ({"pix_display": quote.pix_display} if quote.pix_display is not None else {})


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
                "price_display": summary["price_display"],
                "installments_display": summary["installments_display"],
            }
            | ({"pix_display": summary["pix_display"]} if "pix_display" in summary else {}),
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


# ---------- get_order_status (FR-06, FR-08) ----------

VERIFICATION_FAILED = "Could not verify this order with the information provided."

_verifier = CustomerVerifier()
_delay = DelayAssessor()
_windows = ReturnWindows()


def _fmt_date(value: date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value else None


def _window(window: Window) -> dict[str, Any]:
    return {"deadline": _fmt_date(window.deadline), "is_open": window.is_open}


class GetOrderStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: int = Field(ge=1, description="Order number.")
    email: str | None = Field(
        default=None, min_length=3, description="E-mail used on the purchase."
    )
    phone: str | None = Field(
        default=None, min_length=8, description="Phone used on the purchase, any format."
    )

    @model_validator(mode="after")
    def _contact(self) -> GetOrderStatusArgs:
        if not self.email and not self.phone:
            raise ValueError("Give the e-mail or the phone of the purchase")
        return self


class GetOrderStatusTool(Tool):
    name = "get_order_status"
    description = (
        "Look up ONE order: status, tracking, delay and return/warranty deadlines. Requires the "
        "order number AND the e-mail or phone used on the purchase; ask the customer for both "
        "before calling. Never guess contacts."
    )
    args_model = GetOrderStatusArgs

    def run(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, GetOrderStatusArgs)
        data = self.context.data
        order = data.get_order(args.order_id)
        customer = data.get_customer(order.customer_id) if order else None
        if order is None or customer is None:
            raise ToolError("verification_failed", VERIFICATION_FAILED)
        if not _verifier.verify(customer, email=args.email, phone=args.phone):
            raise ToolError("verification_failed", VERIFICATION_FAILED)

        today = self.context.now().astimezone(STORE_TZ).date()
        language = CUSTOMER_LANGUAGE.get()
        items = []
        for item in data.order_items(order.id):
            product = data.get_product(item.product_id)
            items.append({"name": product.name if product else "?", "quantity": item.quantity})
        delay = _delay.assess(order, today)
        windows = _windows.compute(order, today)
        notes: list[str] = []
        if delay.is_delayed:
            notes.append(
                "Order is delayed: apologize and offer escalate_to_human with reason "
                "'delayed_order'."
            )
        return_windows = None
        if windows is not None:
            return_windows = {
                "receipt_date_assumed": _fmt_date(windows.receipt_date),
                "regret": _window(windows.regret),
                "preference_exchange": _window(windows.preference_exchange),
                "defect_exchange": _window(windows.defect_exchange),
                "legal_warranty": _window(windows.legal_warranty),
                "conditions_topic": "returns",
            }
            notes.append("Receipt date is assumed to be the estimated delivery date (A-11).")
            notes.append(
                "The regret right applies to online purchases: confirm with the customer (A-10)."
            )
            notes.append("For the conditions, call get_store_policy with topic 'returns'.")
        return {
            "verified": True,
            "customer_first_name": customer.first_name,
            "order": {
                "id": order.id,
                "order_date": _fmt_date(order.order_date),
                "status": order.status.value,
                "status_label": order.status.label(language),
                "items": items,
                "total": str(order.total),
                "payment_method": order.payment_method.label(language),
                "tracking_code": order.tracking_code,
                "estimated_delivery": _fmt_date(order.estimated_delivery),
                "cancellation_reason": order.notes if order.status.value == "cancelled" else None,
            },
            "delay": {"is_delayed": delay.is_delayed, "reason": delay.reason},
            "return_windows": return_windows,
            "notes": notes,
        }


# ---------- get_store_policy (FR-07, FR-08, FR-10) ----------


# Seasonal campaigns named in the manual (§6.1) and in promotions.csv; "Oferta Especial"
# and "Desconto Exclusivo" are product promotions, not campaigns (AS-79)
SEASONAL_CAMPAIGNS = (
    "Aniversário da Loja",
    "Black Friday",
    "Volta às Aulas",
    "Queima de Estoque",
    "Semana do Músico",
    "Liquidação de Inverno",
    "Promoção de Páscoa",
)
CAMPAIGN_NOTE = (
    "active_campaign_promotions comes from the store data. When it is empty, no seasonal "
    "campaign is active now: you may cite the manual's usual ranges as information, but "
    "never promise a discount or a start date; suggest following the store's redes sociais."
)


def active_campaign_promotions(data: StoreData) -> list[dict[str, Any]]:
    campaigns = {normalize_text(name) for name in SEASONAL_CAMPAIGNS}
    found = []
    for promotion in data.active_promotions():
        product = data.get_product(promotion.product_id)
        if product is not None and normalize_text(promotion.description) in campaigns:
            found.append(
                {
                    "campaign": promotion.description,
                    "product": product.name,
                    "discount_percent": promotion.discount_percent,
                }
            )
    return found


class GetStorePolicyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: PolicyTopic = Field(description="Policy topic to read.")


class GetStorePolicyTool(Tool):
    name = "get_store_policy"
    description = (
        "Read the store's official policy for one topic: hours, payment, returns, shipping, "
        "promotions, warranty, privacy, contact (address, phone, e-mail) or scope (what the "
        "store sells and service rules). Use it for any question about rules, conditions or "
        "store information; answer only from the returned text."
    )
    args_model = GetStorePolicyArgs

    def run(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, GetStorePolicyArgs)
        section = self.context.policies.get(args.topic)
        payload: dict[str, Any] = {
            "topic": section.topic.value,
            "manual_sections": list(section.sections),
            "manual_version": MANUAL_VERSION,
            "text": section.text,
        }
        if section.topic is PolicyTopic.PROMOTIONS:
            payload["active_campaign_promotions"] = active_campaign_promotions(self.context.data)
            payload["note"] = CAMPAIGN_NOTE
        return payload


# ---------- escalate_to_human (FR-13, FR-17, R-15, R-16) ----------

STORE_CONTACTS = {
    "phone_whatsapp": "(67) 3341-4444",  # A-01
    "email": "contato@emporiodamusica.com.br",  # A-02
    "address": "Rua 14 de Maio, 3200 — Centro, Campo Grande/MS, CEP 79202-333",
}
RESPONSE_TIME = {"pt": "até 24 horas úteis", "en": "within 24 business hours"}  # policy 7.3
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"\(?\d{2}\)?[\s-]?\d{4,5}-?\d{4}")
_hours = StoreHours()


def mask_contacts(text: str) -> str:
    """Mask any e-mail or phone the model put in a summary (NFR-03)."""
    text = _EMAIL.sub(lambda m: mask_email(m.group(0)), text)
    return _PHONE.sub(lambda m: mask_phone(m.group(0)), text)


class EscalateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal[
        "complaint",
        "delayed_order",
        "special_freight_quote",
        "data_privacy_request",
        "product_not_in_catalog",
        "unanswered_question",
        "other",
    ] = Field(description="Why the case goes to a human.")
    summary: str = Field(
        min_length=5,
        max_length=500,
        description="One or two sentences for the attendant, without e-mails or phone numbers.",
    )
    order_id: int | None = Field(default=None, ge=1, description="Related order, if any.")


class EscalateToHumanTool(Tool):
    name = "escalate_to_human"
    description = (
        "Hand the case to a human attendant. Use for complaints, delayed orders, special "
        "freight quotes, data-privacy requests, items the store has no data for, or whenever "
        "you cannot answer safely from the tools. Tell the customer the protocol number, the "
        "response time and the contacts."
    )
    args_model = EscalateArgs

    def run(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, EscalateArgs)
        now = self.context.now().astimezone(STORE_TZ)
        protocol = self._next_protocol(now)
        self._append_log(
            {
                "protocol": protocol,
                "created_at": now.isoformat(timespec="seconds"),
                "reason": args.reason,
                "summary": mask_contacts(args.summary),
                "order_id": args.order_id,
            }
        )
        status = _hours.status(now)
        language = CUSTOMER_LANGUAGE.get()
        next_opening = None
        note = None
        if not status.is_open and status.next_opening is not None:
            opening = status.next_opening
            next_opening = describe_opening(opening, language)
            note = (
                "Store is closed now: tell the customer the team replies from the next "
                "opening (R-15)."
            )
        return {
            "protocol": protocol,
            "response_time": RESPONSE_TIME[language],
            "store_open_now": status.is_open,
            "next_opening": next_opening,
            "contacts": STORE_CONTACTS,
            "note": note,
        }

    def _next_protocol(self, now: datetime) -> str:
        prefix = f"EM-{now:%Y%m%d}-"
        count = 0
        path = self.context.escalation_log
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and json.loads(line).get("protocol", "").startswith(prefix):
                    count += 1
        return f"{prefix}{count + 1:04d}"

    def _append_log(self, entry: dict[str, Any]) -> None:
        path = self.context.escalation_log
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _default_tools(context: ToolContext) -> list[Tool]:
    return [
        SearchProductsTool(context),
        GetProductDetailsTool(context),
        GetOrderStatusTool(context),
        GetStorePolicyTool(context),
        EscalateToHumanTool(context),
    ]
