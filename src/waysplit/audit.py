"""Tamper-evident SQLite audit ledger.

Each row commits to its canonical JSON payload and the preceding row hash. The
current head hash can be copied to an external backup or release record to make
later whole-ledger replacement detectable as well.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from waysplit.domain.canonical import canonical_json
from waysplit.domain.models import DomainModel

AUDIT_SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64
_HASH_DOMAIN = b"waysplit-audit-chain-v1\n"


class AuditEntry(DomainModel):
    sequence: int = Field(ge=1)
    occurred_at: str
    event_type: str = Field(min_length=1)
    payload: dict[str, Any]
    previous_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    entry_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuditVerification(DomainModel):
    valid: bool
    entries_checked: int = Field(ge=0)
    head_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_sequence: int | None = Field(default=None, ge=1)
    reason: str | None = None


def _utc_timestamp(value: datetime | None = None) -> str:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return timestamp.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _entry_hash(
    *,
    sequence: int,
    occurred_at: str,
    event_type: str,
    payload: Mapping[str, Any],
    previous_hash: str,
) -> str:
    committed = canonical_json(
        {
            "audit_schema_version": AUDIT_SCHEMA_VERSION,
            "sequence": sequence,
            "occurred_at": occurred_at,
            "event_type": event_type,
            "payload": payload,
            "previous_hash": previous_hash,
        }
    ).encode("utf-8")
    return hashlib.sha256(_HASH_DOMAIN + committed).hexdigest()


class AuditLedger:
    """Append-only application API over a verifiable SQLite hash chain."""

    def __init__(
        self,
        database: str | Path,
        *,
        connection: sqlite3.Connection | None = None,
        lock: Any | None = None,
    ) -> None:
        requested_database = str(database)
        if requested_database == ":memory:":
            self.database = requested_database
        else:
            resolved_database = Path(requested_database).expanduser().resolve()
            resolved_database.parent.mkdir(parents=True, exist_ok=True)
            self.database = str(resolved_database)
        self._lock = lock or threading.RLock()
        self._owns_connection = connection is None
        self._connection = connection or sqlite3.connect(
            self.database,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA busy_timeout = 10000")
            if self.database != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_entries (
                    sequence INTEGER PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL UNIQUE
                );
                """
            )
            row = self._connection.execute(
                "SELECT value FROM audit_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO audit_metadata(key, value) VALUES ('schema_version', ?)",
                    (str(AUDIT_SCHEMA_VERSION),),
                )
            elif row["value"] != str(AUDIT_SCHEMA_VERSION):
                raise RuntimeError(
                    f"unsupported audit schema version {row['value']!r}; "
                    f"expected {AUDIT_SCHEMA_VERSION}"
                )

    def close(self) -> None:
        if self._owns_connection:
            with self._lock:
                self._connection.close()

    def __enter__(self) -> AuditLedger:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def append(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        occurred_at: datetime | None = None,
    ) -> AuditEntry:
        """Atomically append an event and return its committed chain values."""

        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        normalized_event_type = event_type.strip()
        if not isinstance(payload, Mapping):
            raise ValueError("audit payload must be a JSON object")

        payload_json = canonical_json(dict(payload))
        canonical_payload = json.loads(payload_json)
        timestamp = _utc_timestamp(occurred_at)

        with self._lock:
            owns_transaction = not self._connection.in_transaction
            if owns_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            try:
                previous = self._connection.execute(
                    "SELECT sequence, entry_hash FROM audit_entries ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                sequence = 1 if previous is None else int(previous["sequence"]) + 1
                previous_hash = GENESIS_HASH if previous is None else str(previous["entry_hash"])
                entry_hash = _entry_hash(
                    sequence=sequence,
                    occurred_at=timestamp,
                    event_type=normalized_event_type,
                    payload=canonical_payload,
                    previous_hash=previous_hash,
                )
                self._connection.execute(
                    """
                    INSERT INTO audit_entries(
                        sequence, occurred_at, event_type, payload_json,
                        previous_hash, entry_hash
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sequence,
                        timestamp,
                        normalized_event_type,
                        payload_json,
                        previous_hash,
                        entry_hash,
                    ),
                )
                if owns_transaction:
                    self._connection.execute("COMMIT")
            except BaseException:
                if owns_transaction:
                    self._connection.execute("ROLLBACK")
                raise

        return AuditEntry(
            sequence=sequence,
            occurred_at=timestamp,
            event_type=normalized_event_type,
            payload=canonical_payload,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
        )

    def entries(self) -> tuple[AuditEntry, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM audit_entries ORDER BY sequence"
            ).fetchall()
        return tuple(
            AuditEntry(
                sequence=int(row["sequence"]),
                occurred_at=str(row["occurred_at"]),
                event_type=str(row["event_type"]),
                payload=json.loads(row["payload_json"]),
                previous_hash=str(row["previous_hash"]),
                entry_hash=str(row["entry_hash"]),
            )
            for row in rows
        )

    def head_hash(self) -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT entry_hash FROM audit_entries ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        return GENESIS_HASH if row is None else str(row["entry_hash"])

    def contains_bill_fingerprint(
        self,
        fingerprint: str,
        *,
        event_types: frozenset[str] | None = None,
    ) -> bool:
        """Check structured event payloads without depending on SQLite JSON1."""

        invalid_character = any(character not in "0123456789abcdef" for character in fingerprint)
        if len(fingerprint) != 64 or invalid_character:
            raise ValueError("fingerprint must be a lowercase SHA-256 hex digest")
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_type, payload_json FROM audit_entries ORDER BY sequence"
            ).fetchall()
        for row in rows:
            if event_types is not None and str(row["event_type"]) not in event_types:
                continue
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict) and payload.get("bill_fingerprint") == fingerprint:
                return True
        return False

    def contains_event(
        self,
        event_type: str,
        expected_payload: Mapping[str, Any],
    ) -> bool:
        """Check that the chain commits to an event containing the expected fields."""

        normalized_event_type = event_type.strip()
        if not normalized_event_type:
            raise ValueError("event_type must be a non-empty string")
        expected = json.loads(canonical_json(dict(expected_payload)))
        return any(
            entry.event_type == normalized_event_type
            and all(entry.payload.get(key) == value for key, value in expected.items())
            for entry in self.entries()
        )

    def verify(self) -> AuditVerification:
        """Verify canonical payload bytes, row order, links, and every row hash."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM audit_entries ORDER BY sequence"
            ).fetchall()

        expected_sequence = 1
        expected_previous_hash = GENESIS_HASH
        entries_checked = 0
        for row in rows:
            sequence = int(row["sequence"])
            stored_hash = str(row["entry_hash"])
            if sequence != expected_sequence:
                return AuditVerification(
                    valid=False,
                    entries_checked=entries_checked,
                    head_hash=expected_previous_hash,
                    failure_sequence=sequence,
                    reason=f"non-contiguous sequence; expected {expected_sequence}",
                )
            if str(row["previous_hash"]) != expected_previous_hash:
                return AuditVerification(
                    valid=False,
                    entries_checked=entries_checked,
                    head_hash=expected_previous_hash,
                    failure_sequence=sequence,
                    reason="previous_hash does not match the verified chain head",
                )
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                return AuditVerification(
                    valid=False,
                    entries_checked=entries_checked,
                    head_hash=expected_previous_hash,
                    failure_sequence=sequence,
                    reason=f"payload is not valid JSON: {exc}",
                )
            if not isinstance(payload, dict):
                return AuditVerification(
                    valid=False,
                    entries_checked=entries_checked,
                    head_hash=expected_previous_hash,
                    failure_sequence=sequence,
                    reason="payload is not a JSON object",
                )
            if canonical_json(payload) != str(row["payload_json"]):
                return AuditVerification(
                    valid=False,
                    entries_checked=entries_checked,
                    head_hash=expected_previous_hash,
                    failure_sequence=sequence,
                    reason="payload JSON is not in canonical form",
                )

            calculated_hash = _entry_hash(
                sequence=sequence,
                occurred_at=str(row["occurred_at"]),
                event_type=str(row["event_type"]),
                payload=payload,
                previous_hash=str(row["previous_hash"]),
            )
            if calculated_hash != stored_hash:
                return AuditVerification(
                    valid=False,
                    entries_checked=entries_checked,
                    head_hash=expected_previous_hash,
                    failure_sequence=sequence,
                    reason="entry hash does not match the committed row contents",
                )

            entries_checked += 1
            expected_sequence += 1
            expected_previous_hash = stored_hash

        return AuditVerification(
            valid=True,
            entries_checked=entries_checked,
            head_hash=expected_previous_hash,
        )
