from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import waysplit.service as service_module
from waysplit.destinations.splitwise import (
    AmbiguousDestinationError,
    CreatedExpense,
)
from waysplit.domain.fingerprint import bill_fingerprint
from waysplit.domain.gates import evaluate_posting_gate
from waysplit.domain.models import NormalizedBill
from waysplit.errors import ConfirmationError, PostingBlockedError
from waysplit.household import HouseholdConfig, Participant
from waysplit.repository import Repository
from waysplit.service import WaySplitService
from waysplit.settings import Settings


def _ready_service(
    *,
    tmp_path: Path,
    repository: Repository,
    bill: NormalizedBill,
    label: str,
) -> tuple[WaySplitService, str]:
    run = repository.create_run(
        source_sha256=hashlib.sha256(label.encode()).hexdigest(),
        source_name=f"{label}.pdf",
        source_size=128,
        media_type="application/pdf",
        source_path=f"/synthetic/{label}.pdf",
        model_endpoint="http://127.0.0.1:11434",
        model_provider="ollama",
        model_name="synthetic-local",
        model_digest="a" * 64,
    )
    decision = evaluate_posting_gate(bill)
    repository.set_extracting(run.id)
    repository.complete_extraction(
        run.id,
        bill=bill,
        logical_fingerprint=bill_fingerprint(bill),
        reconciliation=decision.reconciliation.model_dump(mode="json"),
        gate=decision.model_dump(mode="json"),
        ingestion_warnings=(),
        blocked=False,
        retain_source=False,
    )
    household = HouseholdConfig(
        participants=(
            Participant(
                id="member-alpha",
                name="Member Alpha",
                weight="1",
                splitwise_user_id=101,
            ),
            Participant(
                id="member-beta",
                name="Member Beta",
                weight="1",
                splitwise_user_id=202,
            ),
        ),
        service_owners={
            "service-alpha": "member-alpha",
            "service-beta": "member-beta",
        },
        payer_participant_id="member-alpha",
        splitwise_group_id=44,
    )
    service = WaySplitService(
        settings=Settings(
            data_dir=tmp_path / "data",
        ),
        repository=repository,
    )
    ready = service.create_preview(run.id, household)
    assert ready.status == "ready"
    return service, run.id


@pytest.mark.asyncio
async def test_service_post_happy_path_is_confirmed_reserved_and_verified(
    tmp_path: Path,
    repository: Repository,
    normalized_bill: NormalizedBill,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, run_id = _ready_service(
        tmp_path=tmp_path,
        repository=repository,
        bill=normalized_bill,
        label="service-post-happy",
    )

    class FakeSplitwiseClient:
        def __init__(self, *, access_token: str) -> None:
            assert access_token == "synthetic-access-token"

        async def create_expense(self, **_kwargs: object) -> CreatedExpense:
            return CreatedExpense(expense_id="7001", verified=True, verification_issues=())

    monkeypatch.setattr(service_module, "SplitwiseClient", FakeSplitwiseClient)
    token = service.issue_confirmation(run_id)

    posting = await service.post_to_splitwise(
        run_id,
        confirmation_token=token,
        access_token="synthetic-access-token",
        acknowledged_preview=True,
        accepted_destination_terms=True,
    )

    assert posting.status == "posted"
    assert posting.external_id == "7001"
    assert repository.get_run(run_id).status == "posted"
    started = [
        entry
        for entry in repository.audit.entries()
        if entry.event_type == "posting.submission_started"
    ]
    assert started[-1].payload["consent"]["participant_consent_asserted"] is True


@pytest.mark.asyncio
async def test_service_persists_an_ambiguous_create_without_waiting_for_restart(
    tmp_path: Path,
    repository: Repository,
    normalized_bill: NormalizedBill,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, run_id = _ready_service(
        tmp_path=tmp_path,
        repository=repository,
        bill=normalized_bill,
        label="service-post-ambiguous-json",
    )

    class FakeSplitwiseClient:
        def __init__(self, *, access_token: str) -> None:
            assert access_token == "synthetic-access-token"

        async def create_expense(self, **_kwargs: object) -> CreatedExpense:
            raise AmbiguousDestinationError("Synthetic nested response was unreadable.")

    monkeypatch.setattr(service_module, "SplitwiseClient", FakeSplitwiseClient)
    token = service.issue_confirmation(run_id)

    with pytest.raises(AmbiguousDestinationError):
        await service.post_to_splitwise(
            run_id,
            confirmation_token=token,
            access_token="synthetic-access-token",
            acknowledged_preview=True,
            accepted_destination_terms=True,
        )

    posting = repository.posting_for_run(run_id)
    assert posting is not None
    assert posting.status == "ambiguous"
    assert repository.get_run(run_id).status == "ambiguous"


@pytest.mark.asyncio
async def test_service_rollback_timeout_freezes_retry_and_survives_restart_recovery(
    tmp_path: Path,
    repository: Repository,
    normalized_bill: NormalizedBill,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, run_id = _ready_service(
        tmp_path=tmp_path,
        repository=repository,
        bill=normalized_bill,
        label="service-rollback-ambiguous",
    )
    run = repository.get_run(run_id)
    posting = repository.reserve_posting(
        run_id,
        destination="splitwise",
        request_digest=run.preview_digest or "",
    )
    repository.complete_posting(
        posting.id,
        external_id="7002",
        verified=True,
        response_summary={"verified": True, "verification_issues": []},
    )

    class FakeSplitwiseClient:
        def __init__(self, *, access_token: str) -> None:
            assert access_token == "synthetic-access-token"

        async def verify_expense(self, **_kwargs: object) -> tuple[str, ...]:
            return ()

        async def delete_expense(self, _expense_id: str) -> None:
            raise AmbiguousDestinationError("Synthetic deletion outcome is unknown.")

    monkeypatch.setattr(service_module, "SplitwiseClient", FakeSplitwiseClient)
    token = service.issue_rollback_confirmation(run_id)

    with pytest.raises(AmbiguousDestinationError):
        await service.rollback_splitwise(
            run_id,
            confirmation_token=token,
            access_token="synthetic-access-token",
            confirmation_phrase="DELETE",
            acknowledged_target=True,
        )

    assert repository.get_run(run_id).status == "rollback_ambiguous"
    assert repository.posting_for_run(run_id).status == "rollback_ambiguous"  # type: ignore[union-attr]
    assert repository.recover_interrupted_postings() == 0
    with pytest.raises(ConfirmationError, match="rollback approval"):
        service.issue_rollback_confirmation(run_id)


@pytest.mark.asyncio
async def test_service_successful_rollback_is_verified_and_audited(
    tmp_path: Path,
    repository: Repository,
    normalized_bill: NormalizedBill,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, run_id = _ready_service(
        tmp_path=tmp_path,
        repository=repository,
        bill=normalized_bill,
        label="service-rollback-success",
    )
    run = repository.get_run(run_id)
    posting = repository.reserve_posting(
        run_id,
        destination="splitwise",
        request_digest=run.preview_digest or "",
    )
    repository.complete_posting(
        posting.id,
        external_id="7003",
        verified=False,
        response_summary={
            "verified": False,
            "verification_issues": ["Initial readback unavailable."],
        },
    )

    class FakeSplitwiseClient:
        def __init__(self, *, access_token: str) -> None:
            assert access_token == "synthetic-access-token"

        async def verify_expense(self, **_kwargs: object) -> tuple[str, ...]:
            return ()

        async def delete_expense(self, expense_id: str) -> None:
            assert expense_id == "7003"

    monkeypatch.setattr(service_module, "SplitwiseClient", FakeSplitwiseClient)
    token = service.issue_rollback_confirmation(run_id)

    rolled_back = await service.rollback_splitwise(
        run_id,
        confirmation_token=token,
        access_token="synthetic-access-token",
        confirmation_phrase="DELETE",
        acknowledged_target=True,
    )

    assert rolled_back.status == "rolled_back"
    assert rolled_back.response_summary is not None
    assert rolled_back.response_summary["verification_issues"] == ["Initial readback unavailable."]
    assert rolled_back.response_summary["rollback"]["deleted"] is True
    assert repository.get_run(run_id).status == "rolled_back"


def test_rollback_refuses_a_database_modified_confirmation_plan(
    tmp_path: Path,
    repository: Repository,
    normalized_bill: NormalizedBill,
) -> None:
    service, run_id = _ready_service(
        tmp_path=tmp_path,
        repository=repository,
        bill=normalized_bill,
        label="rollback-corrupted-plan",
    )
    run = repository.get_run(run_id)
    posting = repository.reserve_posting(
        run_id,
        destination="splitwise",
        request_digest=run.preview_digest or "",
    )
    repository.complete_posting(
        posting.id,
        external_id="7004",
        verified=True,
        response_summary={"verified": True, "verification_issues": []},
    )
    preview = dict(run.preview or {})
    preview["description"] = "Database-modified expense"
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE runs SET preview_json = ? WHERE id = ?",
            (json.dumps(preview), run_id),
        )

    with pytest.raises(PostingBlockedError, match="integrity check"):
        service.issue_rollback_confirmation(run_id)


def test_rollback_refuses_a_database_modified_external_id(
    tmp_path: Path,
    repository: Repository,
    normalized_bill: NormalizedBill,
) -> None:
    service, run_id = _ready_service(
        tmp_path=tmp_path,
        repository=repository,
        bill=normalized_bill,
        label="rollback-corrupted-external-id",
    )
    run = repository.get_run(run_id)
    posting = repository.reserve_posting(
        run_id,
        destination="splitwise",
        request_digest=run.preview_digest or "",
    )
    repository.complete_posting(
        posting.id,
        external_id="7005",
        verified=True,
        response_summary={"verified": True, "verification_issues": []},
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE postings SET external_id = ? WHERE id = ?",
            ("9999", posting.id),
        )

    with pytest.raises(PostingBlockedError, match="audit chain"):
        service.issue_rollback_confirmation(run_id)
