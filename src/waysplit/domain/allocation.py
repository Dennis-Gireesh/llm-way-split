"""Cent-preserving deterministic household allocation."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from pydantic import Field, field_validator, model_validator

from .fingerprint import bill_fingerprint
from .models import ChargeScope, DomainModel, NormalizedBill
from .money import CENT, ZERO, Money, parse_money

MAX_ALLOCATION_WEIGHT = Decimal("1000000")
MAX_WEIGHT_SCALE = 6


def parse_weight(value: Any) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ValueError(
            "weights must be decimal strings, integers, or Decimals; floats are forbidden"
        )
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        if value > int(MAX_ALLOCATION_WEIGHT):
            raise ValueError("allocation weight exceeds the supported range")
        parsed = Decimal(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if len(stripped) > 14 or not stripped.replace(".", "", 1).isdigit():
            raise ValueError("weight must be a short plain decimal without exponent notation")
        try:
            parsed = Decimal(stripped)
        except InvalidOperation as exc:
            raise ValueError("invalid allocation weight") from exc
    else:
        raise ValueError("weights must be decimal strings, integers, or Decimals")
    parsed_tuple = parsed.as_tuple()
    exponent = parsed_tuple.exponent
    if (
        not isinstance(exponent, int)
        or exponent < -MAX_WEIGHT_SCALE
        or exponent > 6
        or len(parsed_tuple.digits) > 13
    ):
        raise ValueError("allocation weight must have at most six decimal places")
    if not parsed.is_finite() or parsed <= 0 or parsed > MAX_ALLOCATION_WEIGHT:
        raise ValueError(
            f"allocation weight must be greater than zero and at most {MAX_ALLOCATION_WEIGHT}"
        )
    return parsed.normalize()


class AllocationRules(DomainModel):
    """Maps line-scoped charges to owners and account charges to shared weights."""

    service_owners: dict[str, str] = Field(default_factory=dict, max_length=1000)
    shared_weights: dict[str, Decimal] = Field(default_factory=dict, max_length=50)

    @field_validator("service_owners", mode="before")
    @classmethod
    def validate_service_owners(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise ValueError("service_owners must be a mapping")
        normalized: dict[str, str] = {}
        for raw_service, raw_owner in value.items():
            if not isinstance(raw_service, str) or not raw_service.strip():
                raise ValueError("service owner keys must be non-empty strings")
            if not isinstance(raw_owner, str) or not raw_owner.strip():
                raise ValueError("service owners must be non-empty participant IDs")
            service = raw_service.strip()
            if service in normalized:
                raise ValueError(f"duplicate normalized service identifier: {service}")
            normalized[service] = raw_owner.strip()
        return normalized

    @field_validator("shared_weights", mode="before")
    @classmethod
    def validate_shared_weights(cls, value: Any) -> dict[str, Decimal]:
        if not isinstance(value, Mapping):
            raise ValueError("shared_weights must be a mapping")
        normalized: dict[str, Decimal] = {}
        for raw_participant, raw_weight in value.items():
            if not isinstance(raw_participant, str) or not raw_participant.strip():
                raise ValueError("participant IDs must be non-empty strings")
            participant = raw_participant.strip()
            if participant in normalized:
                raise ValueError(f"duplicate normalized participant ID: {participant}")
            normalized[participant] = parse_weight(raw_weight)
        return normalized


class UnresolvedOwnership(DomainModel):
    charge_id: str = Field(min_length=1, max_length=128)
    service_identifier: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=300)


class UnresolvedOwnerError(ValueError):
    def __init__(self, unresolved: tuple[UnresolvedOwnership, ...]) -> None:
        self.unresolved = unresolved
        details = "; ".join(
            f"{item.charge_id}: {item.reason}"
            + (f" ({item.service_identifier})" if item.service_identifier is not None else "")
            for item in unresolved
        )
        super().__init__(f"cannot allocate charges with unresolved ownership: {details}")


class ChargeAllocation(DomainModel):
    charge_id: str = Field(min_length=1, max_length=128)
    amount: Money
    shares: dict[str, Money] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def shares_preserve_amount(self) -> ChargeAllocation:
        if sum(self.shares.values(), start=ZERO) != self.amount:
            raise ValueError("charge allocation shares must sum exactly to the charge amount")
        return self


class BillAllocation(DomainModel):
    bill_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    charges: tuple[ChargeAllocation, ...] = Field(max_length=1000)
    participant_totals: dict[str, Money] = Field(max_length=50)
    allocated_total: Money

    @model_validator(mode="after")
    def totals_are_consistent(self) -> BillAllocation:
        charge_total = sum((charge.amount for charge in self.charges), start=ZERO)
        participant_total = sum(self.participant_totals.values(), start=ZERO)
        if charge_total != self.allocated_total or participant_total != self.allocated_total:
            raise ValueError("bill allocation totals must reconcile exactly")
        calculated_totals: dict[str, Decimal] = {}
        for charge in self.charges:
            for participant, share in charge.shares.items():
                calculated_totals[participant] = calculated_totals.get(participant, ZERO) + share
        if calculated_totals != self.participant_totals:
            raise ValueError("participant totals must equal the exact sum of per-charge shares")
        return self


def _validated_weights(weights: Mapping[str, Decimal | str | int]) -> dict[str, Decimal]:
    if not weights:
        raise ValueError("at least one allocation weight is required")
    normalized: dict[str, Decimal] = {}
    for raw_participant, raw_weight in weights.items():
        if not isinstance(raw_participant, str) or not raw_participant.strip():
            raise ValueError("participant IDs must be non-empty strings")
        participant = raw_participant.strip()
        if participant in normalized:
            raise ValueError(f"duplicate normalized participant ID: {participant}")
        normalized[participant] = parse_weight(raw_weight)
    return normalized


def allocate_largest_remainder(
    amount: Decimal | str | int,
    weights: Mapping[str, Decimal | str | int],
) -> dict[str, Decimal]:
    """Allocate an exact cent amount proportionally with stable tie-breaking.

    Quotas are represented as exact rational numbers. Negative credits are
    allocated as the exact sign inverse of their positive counterpart.
    """

    parsed_amount = parse_money(amount)
    parsed_weights = _validated_weights(weights)
    target_cents = int(parsed_amount / CENT)
    sign = -1 if target_cents < 0 else 1
    absolute_cents = abs(target_cents)

    rational_weights = {
        participant: Fraction(weight) for participant, weight in parsed_weights.items()
    }
    total_weight = sum(rational_weights.values(), start=Fraction(0, 1))
    base_cents: dict[str, int] = {}
    remainders: dict[str, Fraction] = {}

    for participant in sorted(rational_weights):
        quota = Fraction(absolute_cents, 1) * rational_weights[participant] / total_weight
        base = quota.numerator // quota.denominator
        base_cents[participant] = base
        remainders[participant] = quota - base

    cents_left = absolute_cents - sum(base_cents.values())
    remainder_order = sorted(
        remainders,
        key=lambda participant: (-remainders[participant], participant),
    )
    for participant in remainder_order[:cents_left]:
        base_cents[participant] += 1

    result: dict[str, Decimal] = {}
    for participant in sorted(base_cents):
        signed_cents = base_cents[participant] * sign
        result[participant] = ZERO if signed_cents == 0 else Decimal(signed_cents) * CENT

    if sum(result.values(), start=ZERO) != parsed_amount:  # defensive invariant
        raise AssertionError("largest-remainder allocation failed to preserve cents")
    return result


def _ownership_failures(
    bill: NormalizedBill,
    rules: AllocationRules,
) -> tuple[UnresolvedOwnership, ...]:
    failures: list[UnresolvedOwnership] = []
    for charge in bill.charges:
        if charge.scope is ChargeScope.LINE:
            if charge.service_identifier is None:
                failures.append(
                    UnresolvedOwnership(
                        charge_id=charge.charge_id,
                        reason="line-scoped charge has no service_identifier",
                    )
                )
            elif charge.service_identifier not in rules.service_owners:
                failures.append(
                    UnresolvedOwnership(
                        charge_id=charge.charge_id,
                        service_identifier=charge.service_identifier,
                        reason="service_identifier has no configured owner",
                    )
                )
        elif not rules.shared_weights:
            failures.append(
                UnresolvedOwnership(
                    charge_id=charge.charge_id,
                    reason="account-scoped charge requires shared_weights",
                )
            )
    return tuple(failures)


def allocate_bill(bill: NormalizedBill, rules: AllocationRules) -> BillAllocation:
    """Allocate every charge, failing atomically when any owner is unresolved."""

    failures = _ownership_failures(bill, rules)
    if failures:
        raise UnresolvedOwnerError(failures)

    charge_allocations: list[ChargeAllocation] = []
    participant_totals: dict[str, Decimal] = {}

    for charge in bill.charges:
        if charge.scope is ChargeScope.LINE:
            # The preflight above guarantees both values exist.
            owner = rules.service_owners[charge.service_identifier or ""]
            weights: Mapping[str, Decimal | str | int] = {owner: Decimal(1)}
        else:
            weights = rules.shared_weights

        shares = allocate_largest_remainder(charge.amount, weights)
        allocation = ChargeAllocation(
            charge_id=charge.charge_id,
            amount=charge.amount,
            shares=shares,
        )
        charge_allocations.append(allocation)
        for participant, share in shares.items():
            participant_totals[participant] = participant_totals.get(participant, ZERO) + share

    ordered_totals = {
        participant: participant_totals[participant] for participant in sorted(participant_totals)
    }
    allocated_total = sum((charge.amount for charge in bill.charges), start=ZERO)
    return BillAllocation(
        bill_fingerprint=bill_fingerprint(bill),
        charges=tuple(charge_allocations),
        participant_totals=ordered_totals,
        allocated_total=allocated_total,
    )
