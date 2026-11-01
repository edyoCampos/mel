from datetime import date

import pytest
from pydantic import ValidationError

from emporio.models import (
    Availability,
    Customer,
    Order,
    OrderStatus,
    PaymentKind,
    PaymentMethod,
    Product,
    ProductStatus,
)
from emporio.money import Money


def make_product(**overrides: object) -> Product:
    fields: dict[str, object] = {
        "id": 81,
        "name": "Yamaha C40 Nylon Natural",
        "category_id": 5,
        "price": "599.9",
        "description": "Classical guitar",
        "stock_quantity": 12,
        "status": "active",
        "specs": {"strings": "nylon"},
        "string_material": "nylon",
        "string_count": None,
        "created_at": date(2021, 3, 15),
    }
    fields.update(overrides)
    return Product.model_validate(fields)


def test_payment_method_parse_simple() -> None:
    method = PaymentMethod.parse("pix")
    assert method.kind is PaymentKind.PIX
    assert method.installments == 1


def test_payment_method_parse_credit_installments() -> None:
    method = PaymentMethod.parse("credit_12x")
    assert method.kind is PaymentKind.CREDIT
    assert method.installments == 12


def test_payment_method_parse_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        PaymentMethod.parse("cash")


def test_order_status_labels_pt() -> None:
    # R-11
    assert OrderStatus.PENDING.label_pt == "pendente"
    assert OrderStatus.CONFIRMED.label_pt == "confirmado"
    assert OrderStatus.SHIPPED.label_pt == "enviado"
    assert OrderStatus.DELIVERED.label_pt == "entregue"
    assert OrderStatus.CANCELLED.label_pt == "cancelado"


def test_availability_active_with_stock() -> None:
    product = make_product()
    assert product.availability is Availability.AVAILABLE
    assert product.is_sellable


def test_availability_active_without_stock() -> None:
    gf3d = make_product(id=96, name="Giannini GF-3D Dreadnought Sunburst", stock_quantity=0)
    assert gf3d.availability is Availability.OUT_OF_STOCK
    assert not gf3d.is_sellable


def test_availability_discontinued_without_stock() -> None:
    sn7c = make_product(id=113, status="discontinued", stock_quantity=0)
    assert sn7c.availability is Availability.DISCONTINUED


def test_availability_discontinued_with_stock_is_not_sellable() -> None:
    # R-17, A-23: status wins over stock (Ibanez Bass 2X has 13 units)
    ibanez = make_product(id=136, status="discontinued", stock_quantity=13)
    assert ibanez.availability is Availability.DISCONTINUED
    assert not ibanez.is_sellable


def test_availability_coming_soon_with_stock_is_not_sellable() -> None:
    # A-06: Kala KA-ABP-CTG is coming_soon with 2 units
    kala = make_product(id=130, status=ProductStatus.COMING_SOON, stock_quantity=2)
    assert kala.availability is Availability.COMING_SOON
    assert not kala.is_sellable


def test_product_rejects_float_price() -> None:
    with pytest.raises(ValidationError):
        make_product(price=799.9)


def test_product_price_is_money() -> None:
    assert make_product().price == Money("599.90")


def test_customer_first_name() -> None:
    customer = Customer(
        id=2,
        name="Ana Carolina Ferreira",
        phone="(67) 99654-7821",
        email="anacarol.ferreira@coldmail.com",
        city="Campo Grande",
    )
    assert customer.first_name == "Ana"


def test_order_optional_fields_accept_none() -> None:
    order = Order.model_validate(
        {
            "id": 13,
            "customer_id": 8,
            "order_date": date(2026, 3, 12),
            "status": "pending",
            "total": "1899",
            "payment_method": "pix",
            "tracking_code": None,
            "estimated_delivery": None,
            "notes": None,
        }
    )
    assert order.tracking_code is None
    assert order.payment_method.kind is PaymentKind.PIX
    assert order.total == Money("1899")
