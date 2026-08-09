"""Carrier-agnostic normalized bill schema.

The schema intentionally describes financial facts rather than carrier page
layouts. Carrier adapters may help ingestion or validation, but downstream
allocation code depends only on these models.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from .money import Money


class DomainModel(BaseModel):
    """Strict, immutable base model for normalized financial facts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


def _parse_confidence(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("confidence cannot be boolean")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, (str, int, float)):
        try:
            parsed = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("confidence must be a decimal between 0 and 1") from exc
    else:
        raise ValueError("confidence must be a decimal between 0 and 1")
    decimal_tuple = parsed.as_tuple()
    exponent = decimal_tuple.exponent
    if (
        not isinstance(exponent, int)
        or exponent < -6
        or exponent > 0
        or len(decimal_tuple.digits) > 7
    ):
        raise ValueError("confidence must be a plain decimal with at most six places")
    if not parsed.is_finite() or parsed < 0 or parsed > 1:
        raise ValueError("confidence must be between 0 and 1 inclusive")
    return Decimal(0) if parsed == 0 else parsed.normalize()


Confidence = Annotated[
    Decimal,
    BeforeValidator(_parse_confidence),
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "number", "minimum": 0, "maximum": 1},
                {
                    "type": "string",
                    "pattern": r"^(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$",
                    "maxLength": 8,
                },
            ]
        },
        mode="validation",
    ),
]


class ChargeCategory(StrEnum):
    PLAN = "plan"
    SERVICE = "service"
    DEVICE = "device"
    ADD_ON = "add_on"
    USAGE = "usage"
    TAX = "tax"
    FEE = "fee"
    CREDIT = "credit"
    ADJUSTMENT = "adjustment"
    OTHER = "other"


class ChargeScope(StrEnum):
    ACCOUNT = "account"
    LINE = "line"


class EvidenceSource(StrEnum):
    PDF_TEXT = "pdf_text"
    OCR = "ocr"
    VISION = "vision"
    OTHER = "other"


class IssuerMetadata(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    carrier_code: str | None = Field(default=None, min_length=1, max_length=64)


class AccountMetadata(DomainModel):
    account_identifier: str | None = Field(default=None, min_length=1, max_length=128)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    subscriber_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


class StatementMetadata(DomainModel):
    statement_identifier: str | None = Field(default=None, min_length=1, max_length=128)
    issued_on: date
    period_start: date | None = None
    period_end: date | None = None

    @model_validator(mode="after")
    def validate_period(self) -> StatementMetadata:
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("statement period_start and period_end must be supplied together")
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_end < self.period_start
        ):
            raise ValueError("statement period_end cannot be before period_start")
        return self


class BillingMetadata(DomainModel):
    due_on: date | None = None
    autopay_scheduled_on: date | None = None
    billing_name: str | None = Field(default=None, min_length=1, max_length=200)
    billing_address: str | None = Field(default=None, min_length=1, max_length=1000)


class BillTotals(DomainModel):
    """Signed bill totals.

    ``payments_and_credits`` and ``other_adjustments`` retain the sign printed on
    the statement. The balance equation is therefore a direct sum of all four
    components, without hidden sign inversions.
    """

    balance_forward: Money
    payments_and_credits: Money
    current_charges: Money
    other_adjustments: Money
    amount_due: Money


class ChargeEvidence(DomainModel):
    source: EvidenceSource
    page: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1, max_length=500)
    locator: str | None = Field(default=None, min_length=1, max_length=256)


class Charge(DomainModel):
    charge_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    amount: Money
    category: ChargeCategory
    scope: ChargeScope
    service_identifier: str | None = Field(default=None, min_length=1, max_length=128)
    confidence: Confidence
    evidence: tuple[ChargeEvidence, ...] = Field(default=(), max_length=20)


class NormalizedBill(DomainModel):
    """Version 1 of the normalized bill contract consumed by deterministic code."""

    schema_version: Literal["1.0"] = "1.0"
    issuer: IssuerMetadata
    account: AccountMetadata
    statement: StatementMetadata
    billing: BillingMetadata = Field(default_factory=BillingMetadata)
    totals: BillTotals
    charges: tuple[Charge, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def validate_charge_ids(self) -> NormalizedBill:
        charge_ids = [charge.charge_id for charge in self.charges]
        if len(charge_ids) != len(set(charge_ids)):
            raise ValueError("charge_id values must be unique within a bill")
        return self
