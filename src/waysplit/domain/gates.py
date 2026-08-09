"""Explicit, deterministic safety gates for external posting."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .fingerprint import bill_fingerprint
from .models import Confidence, DomainModel, NormalizedBill
from .money import ZERO, Money
from .reconciliation import ReconciliationResult, reconcile_bill


class PostingStatus(StrEnum):
    BLOCKED = "blocked"
    NEEDS_CONFIRMATION = "needs_confirmation"
    APPROVED = "approved"


class GateReasonCode(StrEnum):
    NO_ITEMIZED_CHARGES = "no_itemized_charges"
    LINE_ITEMS_DO_NOT_RECONCILE = "line_items_do_not_reconcile"
    BALANCE_EQUATION_DOES_NOT_RECONCILE = "balance_equation_does_not_reconcile"
    LOW_CONFIDENCE_CHARGES = "low_confidence_charges"
    MISSING_CHARGE_EVIDENCE = "missing_charge_evidence"
    DUPLICATE_STATEMENT = "duplicate_statement"
    EXPLICIT_CONFIRMATION_REQUIRED = "explicit_confirmation_required"


class GateReason(DomainModel):
    code: GateReasonCode
    message: str = Field(min_length=1)
    charge_ids: tuple[str, ...] = ()


class PostingGateConfig(DomainModel):
    reconciliation_tolerance: Money = ZERO
    minimum_charge_confidence: Confidence = Decimal("0.80")
    require_charge_evidence: bool = True

    @field_validator("reconciliation_tolerance")
    @classmethod
    def exact_tolerance(cls, value: Decimal) -> Decimal:
        if value != ZERO:
            raise ValueError("the posting gate requires exact cent-for-cent reconciliation")
        return value


class PostingDecision(DomainModel):
    status: PostingStatus
    approved: bool
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reconciliation: ReconciliationResult
    reasons: tuple[GateReason, ...]

    @model_validator(mode="after")
    def validate_approval(self) -> PostingDecision:
        if self.approved is not (self.status is PostingStatus.APPROVED):
            raise ValueError("approved must be true exactly when status is approved")
        if self.status is PostingStatus.APPROVED and self.reasons:
            raise ValueError("approved decisions cannot contain gate reasons")
        if self.status is not PostingStatus.APPROVED and not self.reasons:
            raise ValueError("non-approved decisions must explain why posting is unavailable")
        return self


def evaluate_posting_gate(
    bill: NormalizedBill,
    *,
    config: PostingGateConfig | None = None,
    already_processed: bool = False,
    confirmed: bool = False,
) -> PostingDecision:
    """Return a posting decision that can never silently bypass confirmation."""

    effective_config = config or PostingGateConfig()
    reconciliation = reconcile_bill(
        bill,
        tolerance=effective_config.reconciliation_tolerance,
    )
    blockers: list[GateReason] = []

    if not bill.charges:
        blockers.append(
            GateReason(
                code=GateReasonCode.NO_ITEMIZED_CHARGES,
                message="A statement with no itemized charges cannot be posted.",
            )
        )

    if not reconciliation.line_items.passed:
        blockers.append(
            GateReason(
                code=GateReasonCode.LINE_ITEMS_DO_NOT_RECONCILE,
                message="Itemized charges do not equal the statement's current charges.",
            )
        )
    if not reconciliation.balance_equation.passed:
        blockers.append(
            GateReason(
                code=GateReasonCode.BALANCE_EQUATION_DOES_NOT_RECONCILE,
                message="Signed statement totals do not equal the amount due.",
            )
        )

    low_confidence = tuple(
        sorted(
            charge.charge_id
            for charge in bill.charges
            if charge.confidence < effective_config.minimum_charge_confidence
        )
    )
    if low_confidence:
        blockers.append(
            GateReason(
                code=GateReasonCode.LOW_CONFIDENCE_CHARGES,
                message="One or more charges are below the configured confidence threshold.",
                charge_ids=low_confidence,
            )
        )

    if effective_config.require_charge_evidence:
        missing_evidence = tuple(
            sorted(charge.charge_id for charge in bill.charges if not charge.evidence)
        )
        if missing_evidence:
            blockers.append(
                GateReason(
                    code=GateReasonCode.MISSING_CHARGE_EVIDENCE,
                    message="One or more charges do not include source evidence.",
                    charge_ids=missing_evidence,
                )
            )

    if already_processed:
        blockers.append(
            GateReason(
                code=GateReasonCode.DUPLICATE_STATEMENT,
                message="This logical statement fingerprint was already processed.",
            )
        )

    if blockers:
        status = PostingStatus.BLOCKED
        approved = False
        reasons = tuple(blockers)
    elif not confirmed:
        status = PostingStatus.NEEDS_CONFIRMATION
        approved = False
        reasons = (
            GateReason(
                code=GateReasonCode.EXPLICIT_CONFIRMATION_REQUIRED,
                message="Review the dry-run preview and explicitly confirm before posting.",
            ),
        )
    else:
        status = PostingStatus.APPROVED
        approved = True
        reasons = ()

    return PostingDecision(
        status=status,
        approved=approved,
        fingerprint=bill_fingerprint(bill),
        reconciliation=reconciliation,
        reasons=reasons,
    )
