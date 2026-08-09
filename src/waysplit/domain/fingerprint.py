"""Stable logical bill fingerprints for duplicate detection."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .canonical import canonical_json
from .models import Charge, NormalizedBill
from .money import format_money

FINGERPRINT_VERSION = 1

_WHITESPACE = re.compile(r"\s+")


def _normalized_text(value: str | None, *, casefold: bool = False) -> str | None:
    if value is None:
        return None
    normalized = _WHITESPACE.sub(" ", value.strip())
    return normalized.casefold() if casefold else normalized


def _logical_charge(charge: Charge) -> dict[str, Any]:
    return {
        "amount": format_money(charge.amount),
        "category": charge.category.value,
        "description": _normalized_text(charge.description, casefold=True),
        "scope": charge.scope.value,
        "service_identifier": _normalized_text(charge.service_identifier),
    }


def logical_bill_payload(bill: NormalizedBill) -> dict[str, Any]:
    """Return logical financial facts, excluding extraction-only metadata.

    Charge IDs, confidence, evidence, and input order are deliberately excluded,
    so retrying extraction cannot evade duplicate detection.
    """

    charges = [_logical_charge(charge) for charge in bill.charges]
    charges.sort(key=canonical_json)
    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "issuer": {
            "name": _normalized_text(bill.issuer.name, casefold=True),
            "carrier_code": _normalized_text(bill.issuer.carrier_code, casefold=True),
        },
        "account": {
            "account_identifier": _normalized_text(bill.account.account_identifier),
            "currency": bill.account.currency,
            "subscriber_name": _normalized_text(bill.account.subscriber_name, casefold=True),
        },
        "statement": {
            "statement_identifier": _normalized_text(bill.statement.statement_identifier),
            "issued_on": bill.statement.issued_on.isoformat(),
            "period_start": (
                bill.statement.period_start.isoformat() if bill.statement.period_start else None
            ),
            "period_end": (
                bill.statement.period_end.isoformat() if bill.statement.period_end else None
            ),
        },
        "billing": {
            "due_on": bill.billing.due_on.isoformat() if bill.billing.due_on else None,
            "autopay_scheduled_on": (
                bill.billing.autopay_scheduled_on.isoformat()
                if bill.billing.autopay_scheduled_on
                else None
            ),
            "billing_name": _normalized_text(bill.billing.billing_name, casefold=True),
            "billing_address": _normalized_text(bill.billing.billing_address, casefold=True),
        },
        "totals": {
            "balance_forward": format_money(bill.totals.balance_forward),
            "payments_and_credits": format_money(bill.totals.payments_and_credits),
            "current_charges": format_money(bill.totals.current_charges),
            "other_adjustments": format_money(bill.totals.other_adjustments),
            "amount_due": format_money(bill.totals.amount_due),
        },
        "charges": charges,
    }


def bill_fingerprint(bill: NormalizedBill) -> str:
    payload = canonical_json(logical_bill_payload(bill)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
