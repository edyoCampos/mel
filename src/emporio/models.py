"""Validated domain entities shared by the whole package."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PlainValidator, field_validator

from emporio.money import Money


def normalize_text(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace: ' Três  Lagoas ' -> 'tres lagoas'."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.lower().split())


def _parse_money(value: Any) -> Money:
    if isinstance(value, Money):
        return value
    return Money(value)  # raises ValueError for float, which Pydantic reports


MoneyField = Annotated[
    Money,
    PlainValidator(_parse_money),
    PlainSerializer(lambda money: str(money.amount), return_type=str),
]


class ProductStatus(StrEnum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    COMING_SOON = "coming_soon"


class Availability(StrEnum):
    AVAILABLE = "available"
    OUT_OF_STOCK = "out_of_stock"
    DISCONTINUED = "discontinued"
    COMING_SOON = "coming_soon"


_ORDER_STATUS_PT = {
    "pending": "pendente",
    "confirmed": "confirmado",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @property
    def label_pt(self) -> str:
        """Portuguese label shown to customers (R-11)."""
        return _ORDER_STATUS_PT[self.value]

    def label(self, language: str = "pt") -> str:
        """Label in the customer's language, 'pt' or 'en'."""
        return self.value if language == "en" else self.label_pt


class PaymentKind(StrEnum):
    PIX = "pix"
    DEBIT = "debit"
    CREDIT = "credit"
    BOLETO = "boleto"


_CREDIT_PATTERN = re.compile(r"^credit_(\d{1,2})x$")


class PaymentMethod(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: PaymentKind
    installments: int = Field(ge=1, le=12)

    @classmethod
    def parse(cls, raw: str) -> PaymentMethod:
        """Parse 'pix', 'debit', 'boleto' or 'credit_<N>x'."""
        value = raw.strip().lower()
        match = _CREDIT_PATTERN.match(value)
        if match:
            return cls(kind=PaymentKind.CREDIT, installments=int(match.group(1)))
        if value in (PaymentKind.PIX, PaymentKind.DEBIT, PaymentKind.BOLETO):
            return cls(kind=PaymentKind(value), installments=1)
        raise ValueError(f"Unknown payment method: {raw!r}")

    @property
    def label_pt(self) -> str:
        if self.kind is PaymentKind.CREDIT:
            return f"cartão de crédito em {self.installments}x"
        return {
            PaymentKind.PIX: "PIX",
            PaymentKind.DEBIT: "cartão de débito",
            PaymentKind.BOLETO: "boleto",
        }[self.kind]

    def label(self, language: str = "pt") -> str:
        """Label in the customer's language, 'pt' or 'en'."""
        if language != "en":
            return self.label_pt
        if self.kind is PaymentKind.CREDIT:
            return f"credit card in {self.installments}x"
        return {
            PaymentKind.PIX: "PIX",
            PaymentKind.DEBIT: "debit card",
            PaymentKind.BOLETO: "boleto",
        }[self.kind]


class Category(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    description: str


class Product(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    category_id: int
    price: MoneyField
    description: str
    stock_quantity: int = Field(ge=0)
    status: ProductStatus
    specs: dict[str, str]
    string_material: str | None = None  # "nylon" | "steel" (A-21)
    created_at: date

    @property
    def availability(self) -> Availability:
        """Status first, then stock (R-17, A-06, A-23)."""
        if self.status is ProductStatus.DISCONTINUED:
            return Availability.DISCONTINUED
        if self.status is ProductStatus.COMING_SOON:
            return Availability.COMING_SOON
        if self.stock_quantity == 0:
            return Availability.OUT_OF_STOCK
        return Availability.AVAILABLE

    @property
    def is_sellable(self) -> bool:
        return self.availability is Availability.AVAILABLE


class Promotion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    product_id: int
    discount_percent: int = Field(ge=0, le=100)
    description: str
    is_active: bool


class Customer(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    phone: str
    email: str
    city: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


class OrderItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: int
    product_id: int
    quantity: int = Field(ge=1)


class Order(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    customer_id: int
    order_date: date
    status: OrderStatus
    total: MoneyField  # registered total = amount charged (A-07, A-09)
    payment_method: PaymentMethod
    tracking_code: str | None = None
    estimated_delivery: date | None = None
    notes: str | None = None

    @field_validator("payment_method", mode="before")
    @classmethod
    def _parse_payment_method(cls, value: Any) -> Any:
        if isinstance(value, str):
            return PaymentMethod.parse(value)
        return value
