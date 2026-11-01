"""Small builders for domain objects used across tests."""

from datetime import date
from typing import Any

from emporio.models import Customer, Order, Product, Promotion


def make_product(**overrides: Any) -> Product:
    fields: dict[str, Any] = {
        "id": 95,
        "name": "Takamine GD20 Dreadnought Natural",
        "category_id": 5,
        "price": "2199",
        "description": "Dreadnought acoustic guitar",
        "stock_quantity": 5,
        "status": "active",
        "specs": {},
        "created_at": date(2021, 1, 1),
    }
    fields.update(overrides)
    return Product.model_validate(fields)


def make_promotion(
    product: Product, discount_percent: int, is_active: bool, **kw: Any
) -> Promotion:
    fields: dict[str, Any] = {
        "id": kw.pop("id", 1),
        "product_id": product.id,
        "discount_percent": discount_percent,
        "description": kw.pop("description", "Oferta Especial"),
        "is_active": is_active,
    }
    return Promotion.model_validate(fields)


def make_customer(**overrides: Any) -> Customer:
    fields: dict[str, Any] = {
        "id": 2,
        "name": "Ana Carolina Ferreira",
        "phone": "(67) 99654-7821",
        "email": "anacarol.ferreira@coldmail.com",
        "city": "Campo Grande",
    }
    fields.update(overrides)
    return Customer.model_validate(fields)


def make_order(**overrides: Any) -> Order:
    fields: dict[str, Any] = {
        "id": 8,
        "customer_id": 2,
        "order_date": date(2026, 2, 15),
        "status": "shipped",
        "total": "349.9",
        "payment_method": "debit",
        "tracking_code": "BRJL5544332BR",
        "estimated_delivery": date(2026, 2, 28),
        "notes": None,
    }
    fields.update(overrides)
    return Order.model_validate(fields)
