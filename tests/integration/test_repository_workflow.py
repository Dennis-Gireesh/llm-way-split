from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from waysplit.domain.fingerprint import bill_fingerprint
from waysplit.domain.gates import PostingStatus, evaluate_posting_gate
from waysplit.domain.models import NormalizedBill
from waysplit.errors import ConfirmationError, DuplicateStatementError, PostingBlockedError
from waysplit.repository import Repository, RunRecord


def _source_digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _create_run(repository: Repository, label: str) -> RunRecord:
    return repository.create_run(
        source_sha256=_source_digest(label),
        source_name=f"{label}.pdf",
        source_size=128,
        media_type="application/pdf",
        source_path=f"/synthetic/{label}.pdf",
        model_endpoint="http://127.0.0.1:11434",
        model_provider="ollama",
        model_name="example-local",
        model_digest="synthetic-model-digest",
    )


def _complete_extraction(
    repository: Repository,
    run: RunRecord,
    bill: NormalizedBill,
    *,
    logical_fingerprint: str | None = None,
) -> RunRecord:
    decision = evaluate_posting_gate(bill)
    assert decision.status is PostingStatus.NEEDS_CONFIRMATION
    repository.set_extracting(run.id)
    repository.complete_extraction(
        run.id,
        bill=bill,
        logical_fingerprint=logical_fingerprint or bill_fingerprint(bill),
        reconciliation=decision.reconciliation.model_dump(mode="json"),
        gate=decision.model_dump(mode="json"),
        ingestion_warnings=(),
        blocked=False,
        retain_source=False,
    )
    return repository.get_run(run.id)


def _make_ready(
    repository: Repository,
    run: RunRecord,
    bill: NormalizedBill,
    *,
    preview_digest: str = "a" * 64,
) -> RunRecord:
    extracted = _complete_extraction(repository, run, bill)
    repository.save_preview(
        extracted.id,
        allocation={"allocated_total": "45.00"},
        household={"participants": []},
        preview={"destination": "splitwise"},
        preview_digest=preview_digest,
        gate={"status": "needs_confirmation", "destination_blockers": []},
        blocked=False,
    )
    return repository.get_run(extracted.id)


def test_duplicate_source_fingerprint_returns_existing_run(
    repository: Repository,
) -> None:
    existing = _create_run(repository, "same-source")

    with pytest.raises(DuplicateStatementError) as captured:
        _create_run(repository, "same-source")

    assert captured.value.run_id == existing.id
    assert len(repository.list_runs()) == 1


def test_failed_extraction_can_be_reuploaded_with_a_new_run(
    repository: Repository,
) -> None:
    failed = _create_run(repository, "retryable-source")
    repository.set_extracting(failed.id)
    repository.fail_run(
        failed.id,
        code="modelresponse",
        message="The synthetic model response was invalid.",
    )

    retried = _create_run(repository, "retryable-source")

    assert retried.id != failed.id
    assert retried.status == "queued"
    assert repository.get_run(failed.id).status == "failed"
    assert len(repository.list_runs()) == 2


def test_interrupted_extraction_can_be_reuploaded(
    repository: Repository,
) -> None:
    interrupted = _create_run(repository, "interrupted-source")

    paths = repository.recover_interrupted_runs()
    retried = _create_run(repository, "interrupted-source")

    assert paths == ("/synthetic/interrupted-source.pdf",)
    assert repository.get_run(interrupted.id).status == "failed"
    assert retried.id != interrupted.id
    assert retried.status == "queued"


def test_state_change_rolls_back_when_audit_append_fails(
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(repository.audit, "append", fail_audit)

    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        _create_run(repository, "audit-transaction")

    assert repository.list_runs() == ()


def test_future_database_schema_is_rejected_before_application_tables_change(
    tmp_path: Path,
) -> None:
    database = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO schema_migrations VALUES (99, 'future')")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="unsupported database schema version 99"):
        Repository(database)

    check = sqlite3.connect(database)
    try:
        tables = {
            row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        check.close()
    assert tables == {"schema_migrations"}


def test_legacy_source_constraint_migration_creates_verified_restore_backup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations VALUES (1, 'legacy');
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            source_sha256 TEXT NOT NULL UNIQUE,
            source_name TEXT NOT NULL,
            source_size INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            source_path TEXT,
            model_endpoint TEXT NOT NULL,
            model_provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            model_digest TEXT,
            ingestion_warnings_json TEXT NOT NULL DEFAULT '[]',
            bill_json TEXT,
            reconciliation_json TEXT,
            gate_json TEXT,
            allocation_json TEXT,
            household_json TEXT,
            preview_json TEXT,
            preview_digest TEXT,
            logical_fingerprint TEXT,
            error_code TEXT,
            error_message TEXT
        );
        """
    )
    connection.commit()
    connection.close()

    repository = Repository(database)
    repository.close()

    backups = tuple(tmp_path.glob("legacy.pre-schema-2.*.sqlite3"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    try:
        assert backup.execute("PRAGMA quick_check").fetchone() == ("ok",)
        backup_versions = backup.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        backup.close()
    assert backup_versions == [(1,)]

    migrated = sqlite3.connect(database)
    try:
        versions = migrated.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        source_indexes = migrated.execute("PRAGMA index_list('runs')").fetchall()
    finally:
        migrated.close()
    assert versions == [(1,), (2,)]
    assert any(
        row[1] == "runs_source_sha256_active_unique" and row[4] == 1 for row in source_indexes
    )


def test_duplicate_logical_fingerprint_is_rejected_across_different_files(
    repository: Repository, normalized_bill: NormalizedBill
) -> None:
    first = _create_run(repository, "source-one")
    second = _create_run(repository, "source-two")
    fingerprint = bill_fingerprint(normalized_bill)
    _complete_extraction(
        repository,
        first,
        normalized_bill,
        logical_fingerprint=fingerprint,
    )
    repository.set_extracting(second.id)
    decision = evaluate_posting_gate(normalized_bill)

    with pytest.raises(DuplicateStatementError) as captured:
        repository.complete_extraction(
            second.id,
            bill=normalized_bill,
            logical_fingerprint=fingerprint,
            reconciliation=decision.reconciliation.model_dump(mode="json"),
            gate=decision.model_dump(mode="json"),
            ingestion_warnings=(),
            blocked=False,
            retain_source=False,
        )

    assert captured.value.run_id == first.id
    assert repository.get_run(second.id).status == "extracting"


def test_invalid_state_transition_is_rejected(
    repository: Repository,
) -> None:
    run = _create_run(repository, "state-transition")
    repository.set_extracting(run.id)

    with pytest.raises(PostingBlockedError, match="changed state"):
        repository.set_extracting(run.id)


def test_confirmation_is_single_use(
    repository: Repository, normalized_bill: NormalizedBill
) -> None:
    ready = _make_ready(repository, _create_run(repository, "single-use"), normalized_bill)
    token = repository.issue_confirmation(ready.id)

    repository.consume_confirmation(
        ready.id,
        token=token,
        preview_digest=ready.preview_digest or "",
    )
    with pytest.raises(ConfirmationError, match="already used"):
        repository.consume_confirmation(
            ready.id,
            token=token,
            preview_digest=ready.preview_digest or "",
        )


def test_confirmation_expiry_and_preview_change_both_fail_closed(
    repository: Repository, normalized_bill: NormalizedBill
) -> None:
    ready = _make_ready(repository, _create_run(repository, "expiry"), normalized_bill)
    expired_token = repository.issue_confirmation(ready.id, ttl_minutes=-1)

    with pytest.raises(ConfirmationError, match="expired"):
        repository.consume_confirmation(
            ready.id,
            token=expired_token,
            preview_digest=ready.preview_digest or "",
        )

    old_token = repository.issue_confirmation(ready.id)
    repository.save_preview(
        ready.id,
        allocation={"allocated_total": "45.00"},
        household={"participants": []},
        preview={"destination": "splitwise", "revision": 2},
        preview_digest="b" * 64,
        gate={"status": "needs_confirmation", "destination_blockers": []},
        blocked=False,
    )
    with pytest.raises(ConfirmationError, match="out of date"):
        repository.consume_confirmation(
            ready.id,
            token=old_token,
            preview_digest="a" * 64,
        )


def test_definite_posting_failure_allows_safe_retry_with_stable_correlation(
    repository: Repository, normalized_bill: NormalizedBill
) -> None:
    ready = _make_ready(repository, _create_run(repository, "safe-retry"), normalized_bill)
    first = repository.reserve_posting(
        ready.id,
        destination="splitwise",
        request_digest=ready.preview_digest or "",
    )
    repository.fail_posting(
        first.id,
        ambiguous=False,
        response_summary={"message": "connection was never established"},
    )

    failed = repository.get_posting(first.id)
    assert failed.status == "failed"
    assert repository.get_run(ready.id).status == "ready"

    retried = repository.reserve_posting(
        ready.id,
        destination="splitwise",
        request_digest=ready.preview_digest or "",
    )
    assert retried.id == first.id
    assert retried.correlation_id == first.correlation_id
    assert retried.status == "submitting"

    repository.complete_posting(
        retried.id,
        external_id="7001",
        verified=False,
        response_summary={"verified": False},
    )
    assert repository.get_run(ready.id).status == "posted_unverified"


def test_ambiguous_posting_cannot_be_retried_blindly(
    repository: Repository, normalized_bill: NormalizedBill
) -> None:
    ready = _make_ready(repository, _create_run(repository, "ambiguous"), normalized_bill)
    posting = repository.reserve_posting(
        ready.id,
        destination="splitwise",
        request_digest=ready.preview_digest or "",
    )
    repository.fail_posting(
        posting.id,
        ambiguous=True,
        response_summary={"message": "response was not received"},
    )

    assert repository.get_posting(posting.id).status == "ambiguous"
    assert repository.get_run(ready.id).status == "ambiguous"
    with pytest.raises(PostingBlockedError, match="not ready"):
        repository.reserve_posting(
            ready.id,
            destination="splitwise",
            request_digest=ready.preview_digest or "",
        )


def test_restart_recovers_in_flight_posting_as_ambiguous(
    tmp_path: Path, normalized_bill: NormalizedBill
) -> None:
    database_path = tmp_path / "restart" / "waysplit.sqlite3"
    before_restart = Repository(database_path)
    ready = _make_ready(
        before_restart,
        _create_run(before_restart, "restart-recovery"),
        normalized_bill,
    )
    posting = before_restart.reserve_posting(
        ready.id,
        destination="splitwise",
        request_digest=ready.preview_digest or "",
    )
    before_restart.close()

    after_restart = Repository(database_path)
    try:
        assert after_restart.recover_interrupted_postings() == 1
        assert after_restart.get_posting(posting.id).status == "ambiguous"
        assert after_restart.get_run(ready.id).status == "ambiguous"
        assert after_restart.recover_interrupted_postings() == 0
    finally:
        after_restart.close()
