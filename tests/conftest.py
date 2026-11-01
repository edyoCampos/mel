"""Shared fixtures: real data, real policies, frozen clock, temporary escalation log."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from emporio.policies import PolicyBook
from emporio.services import STORE_TZ
from emporio.store_data import StoreData
from emporio.tools import ToolContext

ROOT = Path(__file__).resolve().parents[1]
SUNDAY_1_NOV = datetime(2026, 11, 1, 10, 0, tzinfo=STORE_TZ)


@pytest.fixture(scope="session")
def store_data() -> StoreData:
    return StoreData.load(ROOT / "data" / "raw")


@pytest.fixture(scope="session")
def policy_book() -> PolicyBook:
    return PolicyBook.load(ROOT / "data" / "policies.md")


@pytest.fixture
def make_context(
    store_data: StoreData, policy_book: PolicyBook, tmp_path: Path
) -> Callable[..., ToolContext]:
    def build(now: datetime = SUNDAY_1_NOV) -> ToolContext:
        return ToolContext(
            data=store_data,
            policies=policy_book,
            now=lambda: now,
            escalation_log=tmp_path / "logs" / "escalations.jsonl",
        )

    return build
