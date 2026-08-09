from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from waysplit.domain import (
    AccountMetadata,
    BillTotals,
    Charge,
    ChargeCategory,
    ChargeEvidence,
    ChargeScope,
    EvidenceSource,
    GateReasonCode,
    IssuerMetadata,
    NormalizedBill,
    PostingGateConfig,
    PostingStatus,
    StatementMetadata,
    evaluate_posting_gate,
    reconcile_bill,
)


def _charge(
    *,
    charge_id: str = "monthly-plan",
    amount: str = "15.00",
    confidence: str = "0.99",
    with_evidence: bool = True,
) -> Charge:
    evidence = (
        ChargeEvidence(
            source=EvidenceSource.PDF_TEXT,
            page=2,
            text=f"Monthly plan {amount}",
        ),
    )
    return Charge(
        charge_id=charge_id,
        description="Monthly plan",
        amount=amount,
        category=ChargeCategory.PLAN,
        scope=ChargeScope.ACCOUNT,
        confidence=confidence,
        evidence=evidence if with_evidence else (),
    )


def _bill(
    *,
    current_charges: str = "15.00",
    amount_due: str = "95.00",
    charges: tuple[Charge, ...] | None = None,
) -> NormalizedBill:
    return NormalizedBill(
        issuer=IssuerMetadata(name="Example Mobile"),
        account=AccountMetadata(account_identifier="***1234"),
        statement=StatementMetadata(
            statement_identifier="INV-2026-07",
            issued_on=date(2026, 7, 31),
        ),
        totals=BillTotals(
            balance_forward="100.00",
            payments_and_credits="-20.00",
            current_charges=current_charges,
            other_adjustments="0.00",
            amount_due=amount_due,
        ),
        charges=(_charge(),) if charges is None else charges,
    )


def test_signed_balance_and_itemized_charges_reconcile_exactly() -> None:
    result = reconcile_bill(_bill(), tolerance="0.00")

    assert result.reconciled is True
    assert result.line_items.actual == Decimal("15.00")
    assert result.balance_equation.actual == Decimal("95.00")


def test_default_reconciliation_is_exact_but_diagnostic_tolerance_is_explicit() -> None:
    bill = _bill(current_charges="15.01", amount_due="95.01")

    assert reconcile_bill(bill).reconciled is False
    assert reconcile_bill(bill, tolerance="0.01").reconciled is True


def test_posting_gate_refuses_any_nonzero_reconciliation_tolerance() -> None:
    with pytest.raises(ValidationError, match="cent-for-cent"):
        PostingGateConfig(reconciliation_tolerance="0.01")


def test_reconciliation_failure_blocks_posting_even_when_confirmed() -> None:
    bill = _bill(current_charges="15.02", amount_due="95.02")

    decision = evaluate_posting_gate(bill, confirmed=True)

    assert decision.status is PostingStatus.BLOCKED
    assert decision.approved is False
    assert GateReasonCode.LINE_ITEMS_DO_NOT_RECONCILE in {
        reason.code for reason in decision.reasons
    }


def test_safe_bill_still_requires_explicit_confirmation() -> None:
    preview = evaluate_posting_gate(_bill())
    confirmed = evaluate_posting_gate(_bill(), confirmed=True)

    assert preview.status is PostingStatus.NEEDS_CONFIRMATION
    assert preview.approved is False
    assert preview.reasons[0].code is GateReasonCode.EXPLICIT_CONFIRMATION_REQUIRED
    assert confirmed.status is PostingStatus.APPROVED
    assert confirmed.approved is True


def test_confidence_evidence_and_duplicate_checks_are_hard_blocks() -> None:
    bill = _bill(charges=(_charge(confidence="0.79", with_evidence=False),))

    decision = evaluate_posting_gate(bill, already_processed=True, confirmed=True)
    reason_codes = {reason.code for reason in decision.reasons}

    assert decision.status is PostingStatus.BLOCKED
    assert reason_codes == {
        GateReasonCode.LOW_CONFIDENCE_CHARGES,
        GateReasonCode.MISSING_CHARGE_EVIDENCE,
        GateReasonCode.DUPLICATE_STATEMENT,
    }


def test_empty_itemization_cannot_pass_posting_gate() -> None:
    bill = NormalizedBill(
        issuer=IssuerMetadata(name="Example Mobile"),
        account=AccountMetadata(),
        statement=StatementMetadata(issued_on=date(2026, 7, 31)),
        totals=BillTotals(
            balance_forward="0.00",
            payments_and_credits="0.00",
            current_charges="0.00",
            other_adjustments="0.00",
            amount_due="0.00",
        ),
        charges=(),
    )

    decision = evaluate_posting_gate(bill, confirmed=True)

    assert decision.status is PostingStatus.BLOCKED
    assert GateReasonCode.NO_ITEMIZED_CHARGES in {reason.code for reason in decision.reasons}


def test_model_output_fields_are_bounded() -> None:
    payload = _charge().model_dump()
    payload["description"] = "x" * 501
    with pytest.raises(ValidationError, match="at most 500 characters"):
        Charge.model_validate(payload)


def test_gate_threshold_is_configurable_without_float_money() -> None:
    bill = _bill(charges=(_charge(confidence="0.85"),))
    decision = evaluate_posting_gate(
        bill,
        config=PostingGateConfig(minimum_charge_confidence="0.90"),
        confirmed=True,
    )

    assert decision.status is PostingStatus.BLOCKED
