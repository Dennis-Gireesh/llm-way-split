from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from waysplit.domain.allocation import AllocationRules, BillAllocation, parse_weight
from waysplit.domain.canonical import canonical_json
from waysplit.domain.models import NormalizedBill
from waysplit.domain.money import Money, format_money


class HouseholdModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Participant(HouseholdModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=80)
    weight: Decimal = Field(default=Decimal("1"))
    splitwise_user_id: int | None = Field(default=None, gt=0)

    @field_validator("weight", mode="before")
    @classmethod
    def validate_weight(cls, value: Any) -> Decimal:
        return parse_weight(value)

    @field_validator("splitwise_user_id", mode="before")
    @classmethod
    def reject_boolean_destination_user_id(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("Splitwise user ID must be a positive integer")
        return value

    @field_validator("weight", mode="after")
    @classmethod
    def serialize_weight_guard(cls, value: Decimal) -> Decimal:
        return value


class HouseholdConfig(HouseholdModel):
    participants: tuple[Participant, ...] = Field(min_length=1, max_length=50)
    service_owners: dict[str, str] = Field(default_factory=dict)
    payer_participant_id: str | None = None
    splitwise_group_id: int | None = Field(default=None, ge=0)

    @field_validator("splitwise_group_id", mode="before")
    @classmethod
    def reject_boolean_destination_group_id(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("Splitwise group ID must be a non-negative integer")
        return value

    @model_validator(mode="after")
    def validate_references(self) -> HouseholdConfig:
        participant_ids = [participant.id for participant in self.participants]
        if len(participant_ids) != len(set(participant_ids)):
            raise ValueError("participant IDs must be unique")
        destination_ids = [
            participant.splitwise_user_id
            for participant in self.participants
            if participant.splitwise_user_id is not None
        ]
        if len(destination_ids) != len(set(destination_ids)):
            raise ValueError("Splitwise user IDs must map to exactly one participant")
        known = set(participant_ids)
        if self.payer_participant_id is not None and self.payer_participant_id not in known:
            raise ValueError("payer_participant_id must name a participant")
        unknown_owners = sorted(set(self.service_owners.values()) - known)
        if unknown_owners:
            raise ValueError(f"service owners reference unknown participants: {unknown_owners}")
        if any(not service.strip() for service in self.service_owners):
            raise ValueError("service identifiers cannot be blank")
        return self

    def allocation_rules(self) -> AllocationRules:
        return AllocationRules(
            service_owners=self.service_owners,
            shared_weights={
                participant.id: participant.weight for participant in self.participants
            },
        )

    def participant(self, participant_id: str) -> Participant:
        for participant in self.participants:
            if participant.id == participant_id:
                return participant
        raise KeyError(participant_id)

    def json_safe(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        for participant in value["participants"]:
            participant["weight"] = format(Decimal(str(participant["weight"])), "f")
        return value


class PreviewShare(HouseholdModel):
    participant_id: str
    participant_name: str
    splitwise_user_id: int | None
    paid_share: Money
    owed_share: Money


class ExpensePreview(HouseholdModel):
    destination: str = "splitwise"
    description: str
    details: str
    date: str
    currency_code: str
    group_id: int | None
    cost: Money
    payer_participant_id: str | None
    shares: tuple[PreviewShare, ...]
    blockers: tuple[str, ...]

    @property
    def postable(self) -> bool:
        return not self.blockers

    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self).encode()).hexdigest()


def build_expense_preview(
    *, bill: NormalizedBill, allocation: BillAllocation, household: HouseholdConfig
) -> ExpensePreview:
    participants = {participant.id: participant for participant in household.participants}
    payer_id = household.payer_participant_id
    total = allocation.allocated_total
    shares: list[PreviewShare] = []
    blockers: list[str] = []

    if total <= 0:
        blockers.append("The allocatable current-charge total must be greater than zero.")
    if payer_id is None:
        blockers.append("Choose which household member paid the statement.")
    if household.splitwise_group_id is None:
        blockers.append("Add a Splitwise group ID (use 0 for an expense outside a group).")

    for participant in household.participants:
        owed = allocation.participant_totals.get(participant.id, Decimal("0.00"))
        paid = total if participant.id == payer_id else Decimal("0.00")
        if owed < 0:
            blockers.append(
                f"{participant.name} has a negative share, which cannot be posted as an expense."
            )
        if participant.splitwise_user_id is None and (owed != 0 or paid != 0):
            blockers.append(f"Add a Splitwise user ID for {participant.name}.")
        shares.append(
            PreviewShare(
                participant_id=participant.id,
                participant_name=participant.name,
                splitwise_user_id=participant.splitwise_user_id,
                paid_share=paid,
                owed_share=owed,
            )
        )

    unknown_totals = sorted(set(allocation.participant_totals) - set(participants))
    if unknown_totals:
        blockers.append("The allocation contains household members that no longer exist.")

    period = bill.statement.period_end or bill.statement.issued_on
    description = f"Mobile statement · {bill.issuer.name} · {period.isoformat()}"[:100]
    details_lines = [
        "WaySplit reconciled preview",
        *(
            f"{share.participant_name}: {bill.account.currency} {format_money(share.owed_share)}"
            for share in shares
        ),
    ]
    return ExpensePreview(
        description=description,
        details="\n".join(details_lines)[:1800],
        date=f"{bill.statement.issued_on.isoformat()}T12:00:00Z",
        currency_code=bill.account.currency,
        group_id=household.splitwise_group_id,
        cost=total,
        payer_participant_id=payer_id,
        shares=tuple(shares),
        blockers=tuple(dict.fromkeys(blockers)),
    )
