from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from waysplit.audit import AuditLedger
from waysplit.domain.canonical import canonical_json
from waysplit.domain.models import NormalizedBill
from waysplit.errors import ConfirmationError, DuplicateStatementError, PostingBlockedError

RUN_STATUSES = {
    "queued",
    "extracting",
    "needs_review",
    "blocked",
    "ready",
    "submitting",
    "posted",
    "posted_unverified",
    "rollback_submitting",
    "rollback_ambiguous",
    "failed",
    "ambiguous",
    "rolled_back",
}
SCHEMA_VERSION = 2

_RUN_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "status",
    "source_sha256",
    "source_name",
    "source_size",
    "media_type",
    "source_path",
    "model_endpoint",
    "model_provider",
    "model_name",
    "model_digest",
    "ingestion_warnings_json",
    "bill_json",
    "reconciliation_json",
    "gate_json",
    "allocation_json",
    "household_json",
    "preview_json",
    "preview_digest",
    "logical_fingerprint",
    "error_code",
    "error_message",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    created_at: str
    updated_at: str
    status: str
    source_sha256: str
    source_name: str
    source_size: int
    media_type: str
    source_path: str | None
    model_endpoint: str
    model_provider: str
    model_name: str
    model_digest: str | None
    ingestion_warnings: tuple[str, ...]
    bill: NormalizedBill | None
    reconciliation: dict[str, Any] | None
    gate: dict[str, Any] | None
    allocation: dict[str, Any] | None
    household: dict[str, Any] | None
    preview: dict[str, Any] | None
    preview_digest: str | None
    logical_fingerprint: str | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class PostingRecord:
    id: str
    run_id: str
    destination: str
    status: str
    correlation_id: str
    request_digest: str
    external_id: str | None
    created_at: str
    updated_at: str
    response_summary: dict[str, Any] | None


class Repository:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.database_path = database_path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            database_path,
            isolation_level=None,
            check_same_thread=False,
            timeout=10,
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._initialize()
            database_path.chmod(0o600)
            self.audit = AuditLedger(
                database_path,
                connection=self._connection,
                lock=self._lock,
            )
        except BaseException:
            self._connection.close()
            raise

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA busy_timeout = 10000")
            migrations_exist = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            if migrations_exist is not None:
                row = self._connection.execute(
                    "SELECT MAX(version) AS version FROM schema_migrations"
                ).fetchone()
                found_version = int(row["version"] or 0)
                if found_version > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"unsupported database schema version {found_version}; "
                        f"this release supports up to {SCHEMA_VERSION}"
                    )
            if self._source_hash_constraint_needs_migration():
                self._create_pre_migration_backup()
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_size INTEGER NOT NULL CHECK (source_size >= 0),
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

                CREATE UNIQUE INDEX IF NOT EXISTS runs_logical_fingerprint_unique
                    ON runs(logical_fingerprint)
                    WHERE logical_fingerprint IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS runs_source_sha256_active_unique
                    ON runs(source_sha256)
                    WHERE status != 'failed';
                CREATE INDEX IF NOT EXISTS runs_created_at_index ON runs(created_at DESC);

                CREATE TABLE IF NOT EXISTS confirmations (
                    token_hash TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    preview_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS confirmations_run_index ON confirmations(run_id);

                CREATE TABLE IF NOT EXISTS postings (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL,
                    correlation_id TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    external_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    response_summary_json TEXT,
                    UNIQUE(run_id, destination)
                );
                CREATE INDEX IF NOT EXISTS postings_external_index
                    ON postings(destination, external_id);

                CREATE TABLE IF NOT EXISTS household_config (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)",
                (utc_now(),),
            )
            self._migrate_source_hash_index()
            self._connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )

    def _migrate_source_hash_index(self) -> None:
        """Replace the pre-release global source hash constraint with an active-run index."""

        if not self._source_hash_constraint_needs_migration():
            return

        column_list = ", ".join(_RUN_COLUMNS)
        self._connection.execute("PRAGMA foreign_keys = OFF")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """
                CREATE TABLE runs_v2 (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_size INTEGER NOT NULL CHECK (source_size >= 0),
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
                )
                """
            )
            self._connection.execute(
                f"INSERT INTO runs_v2 ({column_list}) "  # noqa: S608 - static column names
                f"SELECT {column_list} FROM runs"
            )
            self._connection.execute("DROP TABLE runs")
            self._connection.execute("ALTER TABLE runs_v2 RENAME TO runs")
            self._connection.execute(
                """
                CREATE UNIQUE INDEX runs_logical_fingerprint_unique
                    ON runs(logical_fingerprint)
                    WHERE logical_fingerprint IS NOT NULL
                """
            )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX runs_source_sha256_active_unique
                    ON runs(source_sha256)
                    WHERE status != 'failed'
                """
            )
            self._connection.execute("CREATE INDEX runs_created_at_index ON runs(created_at DESC)")
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        finally:
            self._connection.execute("PRAGMA foreign_keys = ON")
        if self._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("database migration left invalid foreign-key references")

    def _source_hash_constraint_needs_migration(self) -> bool:
        indexes = self._connection.execute("PRAGMA index_list('runs')").fetchall()
        for index in indexes:
            if not bool(index["unique"]):
                continue
            columns = self._connection.execute(f"PRAGMA index_info('{index['name']}')").fetchall()
            if [str(column["name"]) for column in columns] == ["source_sha256"] and str(
                index["origin"]
            ) == "u":
                return True
        return False

    def _create_pre_migration_backup(self) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.database_path.with_name(
            f"{self.database_path.stem}.pre-schema-{SCHEMA_VERSION}.{timestamp}.sqlite3"
        )
        backup_connection = sqlite3.connect(backup_path, isolation_level=None)
        try:
            self._connection.backup(backup_connection)
            result = backup_connection.execute("PRAGMA quick_check").fetchone()
            if result is None or str(result[0]).lower() != "ok":
                raise RuntimeError(
                    "the pre-migration database backup failed integrity verification"
                )
        except BaseException:
            backup_connection.close()
            backup_path.unlink(missing_ok=True)
            raise
        else:
            backup_connection.close()
        backup_path.chmod(0o600)
        return backup_path

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Couple operational state and its audit event in one durable transaction."""

        with self._lock:
            if self._connection.in_transaction:
                raise RuntimeError("nested repository transaction")
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self.audit.close()
        with self._lock:
            self._connection.close()

    def integrity_check(self) -> bool:
        with self._lock:
            rows = self._connection.execute("PRAGMA quick_check").fetchall()
        return len(rows) == 1 and str(rows[0][0]).lower() == "ok"

    def recover_interrupted_postings(self) -> int:
        """Block retries after a crash in either non-idempotent destination window."""

        now = utc_now()
        with self._transaction():
            rows = self._connection.execute(
                """
                SELECT id, run_id, status FROM postings
                WHERE status IN ('submitting', 'rollback_submitting')
                """
            ).fetchall()
            for row in rows:
                is_rollback = row["status"] == "rollback_submitting"
                posting_status = "rollback_ambiguous" if is_rollback else "ambiguous"
                self._connection.execute(
                    "UPDATE postings SET status = ?, updated_at = ? WHERE id = ?",
                    (posting_status, now, row["id"]),
                )
                self._connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                    (posting_status, now, row["run_id"]),
                )
                self.audit.append(
                    (
                        "posting.rollback_recovered_as_ambiguous"
                        if is_rollback
                        else "posting.recovered_as_ambiguous"
                    ),
                    {"posting_id": row["id"], "run_id": row["run_id"]},
                )
        return len(rows)

    def recover_interrupted_runs(self) -> tuple[str, ...]:
        """Fail unfinished extraction jobs after restart and return temporary paths to erase."""

        now = utc_now()
        with self._transaction():
            rows = self._connection.execute(
                "SELECT id, source_path FROM runs WHERE status IN ('queued', 'extracting')"
            ).fetchall()
            self._connection.execute(
                """
                UPDATE runs SET status = 'failed', updated_at = ?, source_path = NULL,
                    error_code = 'interrupted',
                    error_message = 'The app restarted during extraction. ' ||
                        'Upload the statement again.'
                WHERE status IN ('queued', 'extracting')
                """,
                (now,),
            )
            for row in rows:
                self.audit.append("run.recovered_as_failed", {"run_id": row["id"]})
        return tuple(str(row["source_path"]) for row in rows if row["source_path"])

    def create_run(
        self,
        *,
        source_sha256: str,
        source_name: str,
        source_size: int,
        media_type: str,
        source_path: str,
        model_endpoint: str,
        model_provider: str,
        model_name: str,
        model_digest: str | None,
    ) -> RunRecord:
        run_id = secrets.token_hex(16)
        now = utc_now()
        try:
            with self._transaction():
                self._connection.execute(
                    """
                    INSERT INTO runs(
                        id, created_at, updated_at, status, source_sha256, source_name,
                        source_size, media_type, source_path, model_endpoint,
                        model_provider, model_name, model_digest
                    ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        now,
                        now,
                        source_sha256,
                        source_name,
                        source_size,
                        media_type,
                        source_path,
                        model_endpoint,
                        model_provider,
                        model_name,
                        model_digest,
                    ),
                )
                self.audit.append(
                    "run.created",
                    {
                        "run_id": run_id,
                        "source_sha256": source_sha256,
                        "source_size": source_size,
                        "media_type": media_type,
                        "model_provider": model_provider,
                        "model_name": model_name,
                        "model_digest": model_digest,
                    },
                )
        except sqlite3.IntegrityError as exc:
            existing = self._connection.execute(
                """
                SELECT id FROM runs
                WHERE source_sha256 = ? AND status != 'failed'
                ORDER BY created_at DESC LIMIT 1
                """,
                (source_sha256,),
            ).fetchone()
            if existing is not None:
                raise DuplicateStatementError(str(existing["id"])) from exc
            raise
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            row = self._connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run_from_row(row)

    def list_runs(self, *, limit: int = 20) -> tuple[RunRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()
        return tuple(_run_from_row(row) for row in rows)

    def set_extracting(self, run_id: str) -> None:
        with self._transaction():
            self._transition(run_id, from_statuses={"queued", "failed"}, to_status="extracting")
            self.audit.append("run.extraction_started", {"run_id": run_id})

    def complete_extraction(
        self,
        run_id: str,
        *,
        bill: NormalizedBill,
        logical_fingerprint: str,
        reconciliation: dict[str, Any],
        gate: dict[str, Any],
        ingestion_warnings: tuple[str, ...],
        blocked: bool,
        retain_source: bool,
    ) -> None:
        now = utc_now()
        status = "blocked" if blocked else "needs_review"
        with self._transaction():
            try:
                cursor = self._connection.execute(
                    """
                    UPDATE runs SET
                        updated_at = ?, status = ?,
                        source_path = CASE WHEN ? THEN source_path ELSE NULL END,
                        ingestion_warnings_json = ?, bill_json = ?, reconciliation_json = ?,
                        gate_json = ?, logical_fingerprint = ?, allocation_json = NULL,
                        household_json = NULL, preview_json = NULL, preview_digest = NULL,
                        error_code = NULL, error_message = NULL
                    WHERE id = ? AND status = 'extracting'
                    """,
                    (
                        now,
                        status,
                        int(retain_source),
                        canonical_json(ingestion_warnings),
                        canonical_json(bill),
                        canonical_json(reconciliation),
                        canonical_json(gate),
                        logical_fingerprint,
                        run_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = self._connection.execute(
                    "SELECT id FROM runs WHERE logical_fingerprint = ? AND id != ?",
                    (logical_fingerprint, run_id),
                ).fetchone()
                if existing is not None:
                    raise DuplicateStatementError(str(existing["id"])) from exc
                raise
            if cursor.rowcount != 1:
                raise PostingBlockedError("The run is not in an extractable state.")
            model_row = self._connection.execute(
                "SELECT model_name FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            self.audit.append(
                "run.extraction_completed",
                {
                    "run_id": run_id,
                    "bill_fingerprint": logical_fingerprint,
                    "reconciled": bool(reconciliation.get("reconciled")),
                    "blocked": blocked,
                    "model_name": str(model_row["model_name"]),
                },
            )

    def replace_bill_review(
        self,
        run_id: str,
        *,
        bill: NormalizedBill,
        logical_fingerprint: str,
        reconciliation: dict[str, Any],
        gate: dict[str, Any],
        blocked: bool,
    ) -> None:
        now = utc_now()
        status = "blocked" if blocked else "needs_review"
        with self._transaction():
            try:
                cursor = self._connection.execute(
                    """
                    UPDATE runs SET updated_at = ?, status = ?, bill_json = ?,
                        reconciliation_json = ?, gate_json = ?, logical_fingerprint = ?,
                        allocation_json = NULL, household_json = NULL, preview_json = NULL,
                        preview_digest = NULL, error_code = NULL, error_message = NULL
                    WHERE id = ? AND status IN ('blocked', 'needs_review', 'ready', 'failed')
                    """,
                    (
                        now,
                        status,
                        canonical_json(bill),
                        canonical_json(reconciliation),
                        canonical_json(gate),
                        logical_fingerprint,
                        run_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = self._connection.execute(
                    "SELECT id FROM runs WHERE logical_fingerprint = ? AND id != ?",
                    (logical_fingerprint, run_id),
                ).fetchone()
                if existing is not None:
                    raise DuplicateStatementError(str(existing["id"])) from exc
                raise
            if cursor.rowcount != 1:
                raise PostingBlockedError("Posted or ambiguous runs cannot be edited.")
            self._connection.execute("DELETE FROM confirmations WHERE run_id = ?", (run_id,))
            self.audit.append(
                "run.bill_reviewed",
                {
                    "run_id": run_id,
                    "bill_fingerprint": logical_fingerprint,
                    "reconciled": bool(reconciliation.get("reconciled")),
                    "blocked": blocked,
                },
            )

    def save_preview(
        self,
        run_id: str,
        *,
        allocation: dict[str, Any],
        household: dict[str, Any],
        preview: dict[str, Any],
        preview_digest: str,
        gate: dict[str, Any],
        blocked: bool,
    ) -> None:
        now = utc_now()
        status = "blocked" if blocked else "ready"
        with self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE runs SET updated_at = ?, status = ?, allocation_json = ?,
                    household_json = ?, preview_json = ?, preview_digest = ?, gate_json = ?,
                    error_code = NULL, error_message = NULL
                WHERE id = ? AND status IN ('blocked', 'needs_review', 'ready')
                """,
                (
                    now,
                    status,
                    canonical_json(allocation),
                    canonical_json(household),
                    canonical_json(preview),
                    preview_digest,
                    canonical_json(gate),
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PostingBlockedError("The run cannot be previewed in its current state.")
            self._connection.execute("DELETE FROM confirmations WHERE run_id = ?", (run_id,))
            fingerprint_row = self._connection.execute(
                "SELECT logical_fingerprint FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            self.audit.append(
                "run.preview_created",
                {
                    "run_id": run_id,
                    "bill_fingerprint": fingerprint_row["logical_fingerprint"],
                    "preview_digest": preview_digest,
                    "blocked": blocked,
                },
            )

    def fail_run(
        self,
        run_id: str,
        *,
        code: str,
        message: str,
        clear_source: bool = True,
    ) -> None:
        now = utc_now()
        with self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE runs SET updated_at = ?, status = 'failed', error_code = ?,
                    error_message = ?,
                    source_path = CASE WHEN ? THEN NULL ELSE source_path END
                WHERE id = ? AND status NOT IN (
                    'posted', 'posted_unverified', 'ambiguous', 'rolled_back'
                )
                """,
                (now, code[:80], message[:500], int(clear_source), run_id),
            )
            if cursor.rowcount == 1:
                self.audit.append("run.failed", {"run_id": run_id, "error_code": code[:80]})

    def save_household(self, config: dict[str, Any]) -> None:
        now = utc_now()
        with self._transaction():
            self._connection.execute(
                """
                INSERT INTO household_config(singleton, config_json, updated_at) VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (canonical_json(config), now),
            )
            self.audit.append(
                "household.updated",
                {"participant_count": len(config.get("participants", []))},
            )

    def get_household(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT config_json FROM household_config WHERE singleton = 1"
            ).fetchone()
        return json.loads(row["config_json"]) if row is not None else None

    def issue_confirmation(self, run_id: str, *, ttl_minutes: int = 15) -> str:
        run = self.get_run(run_id)
        if run.status != "ready" or not run.preview_digest:
            raise ConfirmationError("Create a passing preview before confirming a post.")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now_value = datetime.now(UTC)
        created = now_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
        expires = (
            (now_value + timedelta(minutes=ttl_minutes))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        with self._transaction():
            self._connection.execute(
                "DELETE FROM confirmations WHERE run_id = ? OR expires_at <= ?", (run_id, created)
            )
            self._connection.execute(
                """
                INSERT INTO confirmations(
                    token_hash, run_id, preview_digest, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, run_id, run.preview_digest, created, expires),
            )
            self.audit.append(
                "posting.confirmation_issued",
                {"run_id": run_id, "preview_digest": run.preview_digest, "expires_at": expires},
            )
        return token

    def consume_confirmation(self, run_id: str, *, token: str, preview_digest: str) -> None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = utc_now()
        with self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE confirmations SET used_at = ?
                WHERE token_hash = ? AND run_id = ? AND preview_digest = ?
                    AND used_at IS NULL AND expires_at > ?
                """,
                (now, token_hash, run_id, preview_digest, now),
            )
            if cursor.rowcount != 1:
                raise ConfirmationError(
                    "The confirmation expired, was already used, or is out of date."
                )
            self.audit.append(
                "posting.confirmation_consumed",
                {"run_id": run_id, "preview_digest": preview_digest},
            )

    def issue_rollback_confirmation(self, run_id: str, *, ttl_minutes: int = 15) -> str:
        run = self.get_run(run_id)
        posting = self.posting_for_run(run_id)
        if (
            posting is None
            or posting.status not in {"posted", "posted_unverified"}
            or posting.external_id is None
            or not run.preview_digest
        ):
            raise ConfirmationError(
                "Only a completed app-created expense can receive rollback approval."
            )
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now_value = datetime.now(UTC)
        created = now_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
        expires = (
            (now_value + timedelta(minutes=ttl_minutes))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        with self._transaction():
            self._connection.execute(
                "DELETE FROM confirmations WHERE run_id = ? OR expires_at <= ?", (run_id, created)
            )
            self._connection.execute(
                """
                INSERT INTO confirmations(
                    token_hash, run_id, preview_digest, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, run_id, run.preview_digest, created, expires),
            )
            self.audit.append(
                "posting.rollback_confirmation_issued",
                {
                    "run_id": run_id,
                    "posting_id": posting.id,
                    "external_id": posting.external_id,
                    "preview_digest": run.preview_digest,
                    "expires_at": expires,
                },
            )
        return token

    def consume_rollback_confirmation(
        self, run_id: str, *, token: str, preview_digest: str
    ) -> None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = utc_now()
        with self._transaction():
            cursor = self._connection.execute(
                """
                UPDATE confirmations SET used_at = ?
                WHERE token_hash = ? AND run_id = ? AND preview_digest = ?
                    AND used_at IS NULL AND expires_at > ?
                """,
                (now, token_hash, run_id, preview_digest, now),
            )
            if cursor.rowcount != 1:
                raise ConfirmationError(
                    "The rollback approval expired, was already used, or is out of date."
                )
            self.audit.append(
                "posting.rollback_confirmation_consumed",
                {"run_id": run_id, "preview_digest": preview_digest},
            )

    def reserve_posting(
        self,
        run_id: str,
        *,
        destination: str,
        request_digest: str,
        consent: dict[str, Any] | None = None,
    ) -> PostingRecord:
        posting_id = secrets.token_hex(16)
        correlation_id = posting_correlation_id(run_id, destination)
        now = utc_now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                run = self._connection.execute(
                    "SELECT status, preview_digest FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
                if run is None or run["status"] != "ready":
                    raise PostingBlockedError("The run is not ready to post.")
                if run["preview_digest"] != request_digest:
                    raise PostingBlockedError("The preview changed; review and confirm it again.")
                existing = self._connection.execute(
                    "SELECT id, status FROM postings WHERE run_id = ? AND destination = ?",
                    (run_id, destination),
                ).fetchone()
                if existing is None:
                    self._connection.execute(
                        """
                        INSERT INTO postings(
                            id, run_id, destination, status, correlation_id,
                            request_digest, created_at, updated_at
                        ) VALUES (?, ?, ?, 'submitting', ?, ?, ?, ?)
                        """,
                        (
                            posting_id,
                            run_id,
                            destination,
                            correlation_id,
                            request_digest,
                            now,
                            now,
                        ),
                    )
                elif existing["status"] == "failed":
                    posting_id = str(existing["id"])
                    self._connection.execute(
                        """
                        UPDATE postings SET status = 'submitting', correlation_id = ?,
                            request_digest = ?, updated_at = ?, response_summary_json = NULL
                        WHERE id = ?
                        """,
                        (correlation_id, request_digest, now, posting_id),
                    )
                else:
                    raise PostingBlockedError(
                        "A posting already exists for this run and destination."
                    )
                self._connection.execute(
                    "UPDATE runs SET status = 'submitting', updated_at = ? WHERE id = ?",
                    (now, run_id),
                )
                self.audit.append(
                    "posting.submission_started",
                    {
                        "posting_id": posting_id,
                        "run_id": run_id,
                        "destination": destination,
                        "correlation_id": correlation_id,
                        "request_digest": request_digest,
                        "consent": consent,
                    },
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return self.get_posting(posting_id)

    def complete_posting(
        self,
        posting_id: str,
        *,
        external_id: str,
        verified: bool,
        response_summary: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                posting = self._connection.execute(
                    """
                    SELECT run_id, destination, correlation_id, request_digest
                    FROM postings WHERE id = ? AND status = 'submitting'
                    """,
                    (posting_id,),
                ).fetchone()
                if posting is None:
                    raise PostingBlockedError("The posting is not awaiting a result.")
                completed_status = "posted" if verified else "posted_unverified"
                self._connection.execute(
                    """
                    UPDATE postings SET status = ?, external_id = ?, updated_at = ?,
                        response_summary_json = ? WHERE id = ?
                    """,
                    (
                        completed_status,
                        external_id,
                        now,
                        canonical_json(response_summary),
                        posting_id,
                    ),
                )
                self._connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                    (completed_status, now, posting["run_id"]),
                )
                self.audit.append(
                    "posting.completed",
                    {
                        "posting_id": posting_id,
                        "run_id": posting["run_id"],
                        "destination": posting["destination"],
                        "correlation_id": posting["correlation_id"],
                        "request_digest": posting["request_digest"],
                        "external_id": external_id,
                        "verified": verified,
                    },
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def fail_posting(
        self,
        posting_id: str,
        *,
        ambiguous: bool,
        response_summary: dict[str, Any],
    ) -> None:
        now = utc_now()
        status = "ambiguous" if ambiguous else "failed"
        run_status = "ambiguous" if ambiguous else "ready"
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                posting = self._connection.execute(
                    "SELECT run_id FROM postings WHERE id = ? AND status = 'submitting'",
                    (posting_id,),
                ).fetchone()
                if posting is None:
                    raise PostingBlockedError("The posting is not awaiting a result.")
                self._connection.execute(
                    """
                    UPDATE postings SET status = ?, updated_at = ?, response_summary_json = ?
                    WHERE id = ?
                    """,
                    (status, now, canonical_json(response_summary), posting_id),
                )
                self._connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                    (run_status, now, posting["run_id"]),
                )
                destination_row = self._connection.execute(
                    "SELECT destination FROM postings WHERE id = ?", (posting_id,)
                ).fetchone()
                self.audit.append(
                    "posting.ambiguous" if ambiguous else "posting.failed",
                    {
                        "posting_id": posting_id,
                        "run_id": posting["run_id"],
                        "destination": destination_row["destination"],
                    },
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def reserve_rollback(self, posting_id: str, *, request_digest: str) -> PostingRecord:
        now = utc_now()
        with self._transaction():
            posting = self._connection.execute(
                """
                SELECT postings.run_id, postings.status, postings.external_id,
                    postings.request_digest, runs.preview_digest
                FROM postings JOIN runs ON runs.id = postings.run_id
                WHERE postings.id = ?
                """,
                (posting_id,),
            ).fetchone()
            if (
                posting is None
                or posting["status"] not in {"posted", "posted_unverified"}
                or posting["external_id"] is None
            ):
                raise PostingBlockedError(
                    "Only a completed app-created expense can be rolled back."
                )
            if (
                posting["request_digest"] != request_digest
                or posting["preview_digest"] != request_digest
            ):
                raise PostingBlockedError("The confirmed rollback target changed.")
            previous_status = str(posting["status"])
            prior_response_row = self._connection.execute(
                "SELECT response_summary_json FROM postings WHERE id = ?", (posting_id,)
            ).fetchone()
            prior_response = _json_or_none(prior_response_row["response_summary_json"]) or {}
            self._connection.execute(
                """
                UPDATE postings SET status = 'rollback_submitting', updated_at = ?,
                    response_summary_json = ? WHERE id = ?
                """,
                (
                    now,
                    canonical_json(
                        {
                            "rollback_previous_status": previous_status,
                            "posting_response_summary": prior_response,
                        }
                    ),
                    posting_id,
                ),
            )
            self._connection.execute(
                "UPDATE runs SET status = 'rollback_submitting', updated_at = ? WHERE id = ?",
                (now, posting["run_id"]),
            )
            self.audit.append(
                "posting.rollback_started",
                {
                    "posting_id": posting_id,
                    "run_id": posting["run_id"],
                    "external_id": posting["external_id"],
                    "request_digest": request_digest,
                },
            )
        return self.get_posting(posting_id)

    def fail_rollback(
        self,
        posting_id: str,
        *,
        ambiguous: bool,
        response_summary: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self._transaction():
            posting = self._connection.execute(
                """
                SELECT run_id, external_id, response_summary_json FROM postings
                WHERE id = ? AND status = 'rollback_submitting'
                """,
                (posting_id,),
            ).fetchone()
            if posting is None:
                raise PostingBlockedError("The rollback is not awaiting a result.")
            previous_summary = _json_or_none(posting["response_summary_json"]) or {}
            previous_status = str(previous_summary.get("rollback_previous_status") or "")
            posting_response = previous_summary.get("posting_response_summary")
            if not isinstance(posting_response, dict):
                posting_response = {}
            if previous_status not in {"posted", "posted_unverified"}:
                raise PostingBlockedError("The rollback reservation is incomplete.")
            posting_status = "rollback_ambiguous" if ambiguous else previous_status
            run_status = posting_status
            self._connection.execute(
                """
                UPDATE postings SET status = ?, updated_at = ?, response_summary_json = ?
                WHERE id = ?
                """,
                (
                    posting_status,
                    now,
                    canonical_json(
                        {
                            **posting_response,
                            "message": response_summary.get("message"),
                            "rollback": response_summary,
                        }
                    ),
                    posting_id,
                ),
            )
            self._connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (run_status, now, posting["run_id"]),
            )
            self.audit.append(
                "posting.rollback_ambiguous" if ambiguous else "posting.rollback_failed",
                {
                    "posting_id": posting_id,
                    "run_id": posting["run_id"],
                    "external_id": posting["external_id"],
                },
            )

    def mark_rolled_back(self, posting_id: str, *, response_summary: dict[str, Any]) -> None:
        now = utc_now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                posting = self._connection.execute(
                    """
                    SELECT run_id, external_id, response_summary_json FROM postings
                    WHERE id = ? AND status = 'rollback_submitting'
                    """,
                    (posting_id,),
                ).fetchone()
                if posting is None:
                    raise PostingBlockedError(
                        "Only a completed app-created expense can be rolled back."
                    )
                reservation_summary = _json_or_none(posting["response_summary_json"]) or {}
                posting_response = reservation_summary.get("posting_response_summary")
                if not isinstance(posting_response, dict):
                    raise PostingBlockedError("The rollback reservation is incomplete.")
                self._connection.execute(
                    """
                    UPDATE postings SET status = 'rolled_back', updated_at = ?,
                        response_summary_json = ? WHERE id = ?
                    """,
                    (
                        now,
                        canonical_json(
                            {
                                **posting_response,
                                "rollback": response_summary,
                            }
                        ),
                        posting_id,
                    ),
                )
                self._connection.execute(
                    "UPDATE runs SET status = 'rolled_back', updated_at = ? WHERE id = ?",
                    (now, posting["run_id"]),
                )
                self.audit.append(
                    "posting.rolled_back",
                    {
                        "posting_id": posting_id,
                        "run_id": posting["run_id"],
                        "external_id": posting["external_id"],
                    },
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def get_posting(self, posting_id: str) -> PostingRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM postings WHERE id = ?", (posting_id,)
            ).fetchone()
        if row is None:
            raise KeyError(posting_id)
        return _posting_from_row(row)

    def posting_for_run(self, run_id: str) -> PostingRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM postings WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return _posting_from_row(row) if row is not None else None

    def _transition(self, run_id: str, *, from_statuses: set[str], to_status: str) -> None:
        if to_status not in RUN_STATUSES:
            raise ValueError(f"unknown run status: {to_status}")
        placeholders = ",".join("?" for _ in from_statuses)
        values: list[Any] = [utc_now(), to_status, run_id, *sorted(from_statuses)]
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE runs SET updated_at = ?, status = ? "  # noqa: S608 - placeholders below
                f"WHERE id = ? AND status IN ({placeholders})",
                values,
            )
        if cursor.rowcount != 1:
            raise PostingBlockedError("The run changed state; refresh and try again.")


def _json_or_none(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else None


def posting_correlation_id(run_id: str, destination: str) -> str:
    """Return the stable per-run reference shown before any destination side effect."""

    committed = f"waysplit-posting-reference-v1\n{run_id}\n{destination}".encode()
    return f"WS-{hashlib.sha256(committed).hexdigest()[:12].upper()}"


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    bill = NormalizedBill.model_validate_json(row["bill_json"]) if row["bill_json"] else None
    warnings_value = json.loads(row["ingestion_warnings_json"])
    return RunRecord(
        id=str(row["id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        status=str(row["status"]),
        source_sha256=str(row["source_sha256"]),
        source_name=str(row["source_name"]),
        source_size=int(row["source_size"]),
        media_type=str(row["media_type"]),
        source_path=str(row["source_path"]) if row["source_path"] else None,
        model_endpoint=str(row["model_endpoint"]),
        model_provider=str(row["model_provider"]),
        model_name=str(row["model_name"]),
        model_digest=str(row["model_digest"]) if row["model_digest"] else None,
        ingestion_warnings=tuple(str(value) for value in warnings_value),
        bill=bill,
        reconciliation=_json_or_none(row["reconciliation_json"]),
        gate=_json_or_none(row["gate_json"]),
        allocation=_json_or_none(row["allocation_json"]),
        household=_json_or_none(row["household_json"]),
        preview=_json_or_none(row["preview_json"]),
        preview_digest=str(row["preview_digest"]) if row["preview_digest"] else None,
        logical_fingerprint=(
            str(row["logical_fingerprint"]) if row["logical_fingerprint"] else None
        ),
        error_code=str(row["error_code"]) if row["error_code"] else None,
        error_message=str(row["error_message"]) if row["error_message"] else None,
    )


def _posting_from_row(row: sqlite3.Row) -> PostingRecord:
    return PostingRecord(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        destination=str(row["destination"]),
        status=str(row["status"]),
        correlation_id=str(row["correlation_id"]),
        request_digest=str(row["request_digest"]),
        external_id=str(row["external_id"]) if row["external_id"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        response_summary=_json_or_none(row["response_summary_json"]),
    )
