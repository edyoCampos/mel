"""StoreHours (R-10, R-15, A-17). The store is in America/Campo_Grande, UTC-04:00."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from emporio.services import STORE_TZ, StoreHours

hours = StoreHours()


def local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=STORE_TZ)


def test_store_timezone_is_campo_grande() -> None:
    assert ZoneInfo("America/Campo_Grande") == STORE_TZ
    assert local(2026, 11, 1, 10).utcoffset().total_seconds() == -4 * 3600  # type: ignore[union-attr]


def test_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        hours.status(datetime(2026, 11, 1, 10))


def test_weekday_open() -> None:
    status = hours.status(local(2026, 11, 3, 10))
    assert status.is_open
    assert status.closes_at == local(2026, 11, 3, 18)


def test_opening_is_inclusive() -> None:
    assert hours.status(local(2026, 11, 3, 9)).is_open


def test_closing_is_exclusive() -> None:
    status = hours.status(local(2026, 11, 3, 18))
    assert not status.is_open
    assert status.closed_reason == "outside_hours"
    assert status.next_opening == local(2026, 11, 4, 9)


def test_saturday_closes_at_13() -> None:
    assert hours.status(local(2026, 11, 7, 12, 59)).is_open
    assert not hours.status(local(2026, 11, 7, 13)).is_open


def test_sunday_before_holiday_reopens_tuesday() -> None:
    status = hours.status(local(2026, 11, 1, 10))
    assert not status.is_open
    assert status.closed_reason == "sunday"
    assert status.next_opening == local(2026, 11, 3, 9)


def test_national_holiday_closed() -> None:
    status = hours.status(local(2026, 11, 2, 10))
    assert not status.is_open
    assert status.closed_reason == "holiday:Finados"


def test_saturday_afternoon_next_opening_monday() -> None:
    assert hours.next_opening(local(2026, 11, 7, 15)) == local(2026, 11, 9, 9)


def test_converts_other_timezones() -> None:
    # 13:30 UTC is 09:30 in Campo Grande
    assert hours.status(datetime(2026, 11, 3, 13, 30, tzinfo=UTC)).is_open


def test_describe_mentions_status_and_next_opening() -> None:
    line = hours.describe(local(2026, 11, 1, 10))
    assert "CLOSED" in line
    # Portuguese weekday and date, ready to copy (AS-80)
    assert "Next opening: terça-feira, 03/11/2026, às 09:00" in line
    assert "Finados" in line


def test_describe_open_store() -> None:
    line = hours.describe(local(2026, 11, 3, 10))
    assert "OPEN" in line
    assert "18:00" in line
