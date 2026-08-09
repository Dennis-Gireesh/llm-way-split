"""Deterministic bill-total reconciliation."""

from __future__ import annotations

from decimal import Decimal

from pydantic import model_validator

from .models import DomainModel, NormalizedBill
from .money import ZERO, Money, parse_money


class ReconciliationCheck(DomainModel):
    name: str
    expected: Money
    actual: Money
    difference: Money
    tolerance: Money
    passed: bool

    @model_validator(mode="after")
    def validate_check(self) -> ReconciliationCheck:
        if self.tolerance < 0:
            raise ValueError("reconciliation tolerance cannot be negative")
        if self.difference != self.actual - self.expected:
            raise ValueError("difference must equal actual minus expected")
        if self.passed is not (abs(self.difference) <= self.tolerance):
            raise ValueError("passed must reflect difference within tolerance")
        return self


class ReconciliationResult(DomainModel):
    line_items: ReconciliationCheck
    balance_equation: ReconciliationCheck
    reconciled: bool

    @model_validator(mode="after")
    def validate_summary(self) -> ReconciliationResult:
        expected = self.line_items.passed and self.balance_equation.passed
        if self.reconciled is not expected:
            raise ValueError("reconciled must equal the result of all checks")
        return self


def _check(
    name: str,
    *,
    expected: Decimal,
    actual: Decimal,
    tolerance: Decimal,
) -> ReconciliationCheck:
    difference = actual - expected
    return ReconciliationCheck(
        name=name,
        expected=expected,
        actual=actual,
        difference=difference,
        tolerance=tolerance,
        passed=abs(difference) <= tolerance,
    )


def reconcile_bill(
    bill: NormalizedBill,
    *,
    tolerance: Decimal | str | int = ZERO,
) -> ReconciliationResult:
    """Validate itemization and the signed statement balance equation.

    The default requires exact equality at the currency's cent boundary. A
    non-zero tolerance is available only for explicit diagnostic comparisons;
    WaySplit's posting gate always uses zero tolerance.
    """

    parsed_tolerance = parse_money(tolerance)
    if parsed_tolerance < 0:
        raise ValueError("reconciliation tolerance cannot be negative")

    itemized_total = sum((charge.amount for charge in bill.charges), start=ZERO)
    line_items = _check(
        "line_items_equal_current_charges",
        expected=bill.totals.current_charges,
        actual=itemized_total,
        tolerance=parsed_tolerance,
    )

    calculated_amount_due = (
        bill.totals.balance_forward
        + bill.totals.payments_and_credits
        + bill.totals.current_charges
        + bill.totals.other_adjustments
    )
    balance_equation = _check(
        "signed_balance_equation_equals_amount_due",
        expected=bill.totals.amount_due,
        actual=calculated_amount_due,
        tolerance=parsed_tolerance,
    )

    return ReconciliationResult(
        line_items=line_items,
        balance_equation=balance_equation,
        reconciled=line_items.passed and balance_equation.passed,
    )
