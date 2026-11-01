"""DelayAssessor (A-20)."""

from datetime import date, timedelta

from emporio.services import DelayAssessor
from tests.factories import make_order

delay = DelayAssessor()


def test_shipped_after_estimated_delivery_is_delayed() -> None:
    result = delay.assess(make_order(), date(2026, 11, 1))
    assert result.is_delayed
    assert result.reason == "estimated_delivery_passed"


def test_shipped_before_estimated_delivery_is_not_delayed() -> None:
    result = delay.assess(make_order(), date(2026, 2, 20))
    assert not result.is_delayed
    assert result.reason is None


def test_confirmed_exactly_seven_days_is_not_delayed() -> None:
    order = make_order(status="confirmed", tracking_code=None, estimated_delivery=None)
    assert not delay.assess(order, order.order_date + timedelta(days=7)).is_delayed


def test_confirmed_eight_days_is_delayed() -> None:
    order = make_order(status="confirmed", tracking_code=None, estimated_delivery=None)
    result = delay.assess(order, order.order_date + timedelta(days=8))
    assert result.is_delayed
    assert result.reason == "not_shipped_after_7_days"


def test_delivered_and_cancelled_never_delayed() -> None:
    delivered = make_order(id=7, status="delivered", estimated_delivery=date(2026, 2, 17))
    cancelled = make_order(id=17, status="cancelled", tracking_code=None, estimated_delivery=None)
    today = date(2026, 11, 1)
    assert not delay.assess(delivered, today).is_delayed
    assert not delay.assess(cancelled, today).is_delayed
