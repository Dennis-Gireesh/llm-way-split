from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

from waysplit.audit import AuditLedger
from waysplit.domain import (
    AccountMetadata,
    BillTotals,
    Charge,
    ChargeCategory,
    ChargeEvidence,
    ChargeScope,
    EvidenceSource,
    IssuerMetadata,
    NormalizedBill,
    StatementMetadata,
    bill_fingerprint,
)


def _charge(
    *,
    charge_id: str,
    amount: str,
    confidence: str,
    evidence_text: str,
) -> Charge:
    return Charge(
        charge_id=charge_id,
        description="Monthly service",
        amount=amount,
        category=ChargeCategory.SERVICE,
        scope=ChargeScope.LINE,
        service_identifier="555-0100",
        confidence=confidence,
        evidence=(
            ChargeEvidence(
                source=EvidenceSource.PDF_TEXT,
                page=1,
                text=evidence_text,
            ),
        ),
    )


def _bill(charges: tuple[Charge, ...]) -> NormalizedBill:
    return NormalizedBill(
        issuer=IssuerMetadata(name=" Example   Mobile "),
        account=AccountMetadata(account_identifier=None),
        statement=StatementMetadata(
            statement_identifier=None,
            issued_on=date(2026, 7, 31),
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        ),
        totals=BillTotals(
            balance_forward="0.00",
            payments_and_credits="0.00",
            current_charges="15.00",
            other_adjustments="0.00",
            amount_due="15.00",
        ),
        charges=charges,
    )


def test_fingerprint_ignores_extraction_artifacts_and_charge_order() -> None:
    first = _charge(
        charge_id="extract-a",
        amount="10.00",
        confidence="0.80",
        evidence_text="OCR evidence A",
    )
    second = _charge(
        charge_id="extract-b",
        amount="5.00",
        confidence="0.81",
        evidence_text="OCR evidence B",
    )
    retry_first = _charge(
        charge_id="retry-99",
        amount="10.00",
        confidence="0.99",
        evidence_text="Better vision evidence",
    )
    retry_second = _charge(
        charge_id="retry-100",
        amount="5.00",
        confidence="0.97",
        evidence_text="Different page crop",
    )

    assert bill_fingerprint(_bill((first, second))) == bill_fingerprint(
        _bill((retry_second, retry_first))
    )


def test_fingerprint_changes_when_logical_money_changes() -> None:
    original = _bill(
        (
            _charge(
                charge_id="a",
                amount="15.00",
                confidence="0.99",
                evidence_text="source",
            ),
        )
    )
    changed = NormalizedBill.model_validate(
        {
            **original.model_dump(),
            "totals": {
                **original.totals.model_dump(),
                "current_charges": "15.01",
                "amount_due": "15.01",
            },
            "charges": [
                {
                    **original.charges[0].model_dump(),
                    "amount": "15.01",
                }
            ],
        }
    )

    assert bill_fingerprint(original) != bill_fingerprint(changed)


def test_audit_ledger_hash_chain_verifies_and_finds_fingerprint(tmp_path) -> None:
    database = tmp_path / "audit.sqlite3"
    fingerprint = "a" * 64
    with AuditLedger(database) as ledger:
        first = ledger.append(
            "bill.extracted",
            {"bill_fingerprint": fingerprint, "amount": Decimal("15.00")},
            occurred_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        )
        second = ledger.append(
            "posting.confirmed",
            {"bill_fingerprint": fingerprint, "destination": "splitwise"},
            occurred_at=datetime(2026, 8, 1, 12, 1, tzinfo=UTC),
        )

        verification = ledger.verify()

        assert verification.valid is True
        assert verification.entries_checked == 2
        assert verification.head_hash == second.entry_hash
        assert second.previous_hash == first.entry_hash
        assert ledger.contains_bill_fingerprint(
            fingerprint,
            event_types=frozenset({"posting.confirmed"}),
        )
        assert ledger.entries()[0].payload["amount"] == "15"


def test_audit_ledger_detects_payload_tampering(tmp_path) -> None:
    database = tmp_path / "audit.sqlite3"
    with AuditLedger(database) as ledger:
        ledger.append(
            "posting.succeeded",
            {"bill_fingerprint": "b" * 64, "amount": "15.00"},
        )

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE audit_entries SET payload_json = ? WHERE sequence = 1",
            ('{"amount":"150.00","bill_fingerprint":"' + "b" * 64 + '"}',),
        )
        connection.commit()

    with AuditLedger(database) as ledger:
        verification = ledger.verify()

    assert verification.valid is False
    assert verification.failure_sequence == 1
    assert "hash does not match" in (verification.reason or "")
