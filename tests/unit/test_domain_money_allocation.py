from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from waysplit.domain import (
    AccountMetadata,
    AllocationRules,
    BillTotals,
    Charge,
    ChargeCategory,
    ChargeEvidence,
    ChargeScope,
    EvidenceSource,
    IssuerMetadata,
    NormalizedBill,
    StatementMetadata,
    UnresolvedOwnerError,
    allocate_bill,
    allocate_largest_remainder,
)


def _evidence(text: str = "Monthly plan $10.00") -> tuple[ChargeEvidence, ...]:
    return (ChargeEvidence(source=EvidenceSource.PDF_TEXT, page=1, text=text),)


def _bill(*charges: Charge, current_charges: str = "15.00") -> NormalizedBill:
    return NormalizedBill(
        issuer=IssuerMetadata(name="Example Mobile"),
        account=AccountMetadata(account_identifier=None),
        statement=StatementMetadata(
            statement_identifier="INV-2026-07",
            issued_on=date(2026, 7, 31),
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        ),
        totals=BillTotals(
            balance_forward="100.00",
            payments_and_credits="-20.00",
            current_charges=current_charges,
            other_adjustments="0.00",
            amount_due=str(Decimal("80.00") + Decimal(current_charges)),
        ),
        charges=charges,
    )


def test_money_rejects_floating_point_input() -> None:
    with pytest.raises(ValidationError, match="floats are forbidden"):
        BillTotals(
            balance_forward=0,
            payments_and_credits=0,
            current_charges=10.25,
            other_adjustments=0,
            amount_due="10.25",
        )


def test_largest_remainder_preserves_every_cent_with_stable_ties() -> None:
    shares = allocate_largest_remainder(
        "10.00",
        {"casey": "1", "alex": "1", "blair": "1"},
    )

    assert shares == {
        "alex": Decimal("3.34"),
        "blair": Decimal("3.33"),
        "casey": Decimal("3.33"),
    }
    assert sum(shares.values()) == Decimal("10.00")


def test_negative_credit_is_exact_sign_inverse_without_negative_zero() -> None:
    positive = allocate_largest_remainder("0.01", {"alex": "1", "blair": "1"})
    negative = allocate_largest_remainder("-0.01", {"alex": "1", "blair": "1"})

    assert negative == {participant: -share for participant, share in positive.items()}
    assert negative == {"alex": Decimal("-0.01"), "blair": Decimal("0.00")}
    assert negative["blair"].is_signed() is False


def test_bill_allocation_uses_line_owner_and_shared_weights() -> None:
    bill = _bill(
        Charge(
            charge_id="line-plan",
            description="Line plan",
            amount="10.00",
            category=ChargeCategory.PLAN,
            scope=ChargeScope.LINE,
            service_identifier="555-0100",
            confidence="0.99",
            evidence=_evidence(),
        ),
        Charge(
            charge_id="account-fee",
            description="Account fee",
            amount="5.00",
            category=ChargeCategory.FEE,
            scope=ChargeScope.ACCOUNT,
            confidence="0.98",
            evidence=_evidence("Account fee $5.00"),
        ),
    )
    rules = AllocationRules(
        service_owners={"555-0100": "alex"},
        shared_weights={"alex": "2", "blair": "1"},
    )

    allocation = allocate_bill(bill, rules)

    assert allocation.allocated_total == Decimal("15.00")
    assert allocation.participant_totals == {
        "alex": Decimal("13.33"),
        "blair": Decimal("1.67"),
    }
    assert sum(allocation.participant_totals.values()) == allocation.allocated_total


def test_allocation_reports_every_unresolved_owner_before_allocating() -> None:
    bill = _bill(
        Charge(
            charge_id="missing-service",
            description="Unknown line",
            amount="10.00",
            category=ChargeCategory.SERVICE,
            scope=ChargeScope.LINE,
            confidence="0.99",
            evidence=_evidence(),
        ),
        Charge(
            charge_id="unknown-owner",
            description="Known line but unmapped owner",
            amount="5.00",
            category=ChargeCategory.SERVICE,
            scope=ChargeScope.LINE,
            service_identifier="555-9999",
            confidence="0.99",
            evidence=_evidence(),
        ),
    )

    with pytest.raises(UnresolvedOwnerError) as captured:
        allocate_bill(bill, AllocationRules())

    assert [failure.charge_id for failure in captured.value.unresolved] == [
        "missing-service",
        "unknown-owner",
    ]
    assert "no service_identifier" in str(captured.value)
    assert "no configured owner" in str(captured.value)


def test_allocation_weights_reject_floats() -> None:
    with pytest.raises(ValidationError, match="floats are forbidden"):
        AllocationRules(shared_weights={"alex": 0.5})
