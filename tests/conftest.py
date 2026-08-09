from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from waysplit.domain.models import NormalizedBill
from waysplit.repository import Repository

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def normalized_bill_payload() -> dict[str, object]:
    return json.loads((FIXTURES / "normalized_bill.json").read_text(encoding="utf-8"))


@pytest.fixture
def normalized_bill(normalized_bill_payload: dict[str, object]) -> NormalizedBill:
    return NormalizedBill.model_validate(normalized_bill_payload)


@pytest.fixture
def statement_text() -> str:
    return (FIXTURES / "example_mobile_statement.txt").read_text(encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[Repository]:
    value = Repository(tmp_path / "state" / "waysplit.sqlite3")
    try:
        yield value
    finally:
        value.close()
