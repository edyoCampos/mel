"""ReturnWindows (R-06, R-07, R-08, A-11, A-18). Order 7: ordered 03/02/2026, received 17/02/2026."""

from datetime import date

from emporio.services import ReturnWindows
from tests.factories import make_order

windows = ReturnWindows()

ORDER_7 = make_order(
    id=7,
    customer_id=14,
    order_date=date(2026, 2, 3),
    status="delivered",
    total="159.9",
    payment_method="credit_3x",
    tracking_code="BRKR1122334BR",
    estimated_delivery=date(2026, 2, 17),
)


def test_windows_none_for_undelivered_order() -> None:
    assert windows.compute(make_order(), date(2026, 11, 1)) is None


def test_regret_and_preference_windows_open_inside_seven_days() -> None:
    result = windows.compute(ORDER_7, date(2026, 2, 20))
    assert result is not None
    assert result.receipt_date == date(2026, 2, 17)
    assert result.regret.deadline == date(2026, 2, 24)
    assert result.regret.is_open
    assert result.preference_exchange.deadline == date(2026, 2, 24)
    assert result.preference_exchange.is_open


def test_regret_window_open_on_last_day() -> None:
    result = windows.compute(ORDER_7, date(2026, 2, 24))
    assert result is not None and result.regret.is_open


def test_regret_window_closed_day_after() -> None:
    result = windows.compute(ORDER_7, date(2026, 2, 25))
    assert result is not None and not result.regret.is_open


def test_defect_window_counts_from_order_date() -> None:
    result = windows.compute(ORDER_7, date(2026, 2, 20))
    assert result is not None
    assert result.defect_exchange.deadline == date(2026, 3, 5)


def test_legal_warranty_counts_from_receipt() -> None:
    result = windows.compute(ORDER_7, date(2026, 2, 20))
    assert result is not None
    assert result.legal_warranty.deadline == date(2026, 5, 18)
    assert result.legal_warranty.is_open


def test_all_windows_closed_today() -> None:
    result = windows.compute(ORDER_7, date(2026, 11, 1))
    assert result is not None
    assert not result.regret.is_open
    assert not result.preference_exchange.is_open
    assert not result.defect_exchange.is_open
    assert not result.legal_warranty.is_open
