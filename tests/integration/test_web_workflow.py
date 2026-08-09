from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import waysplit.web as web_module
from waysplit.destinations.splitwise import SplitwiseContext, SplitwiseGroup, SplitwiseMember
from waysplit.domain.fingerprint import bill_fingerprint
from waysplit.domain.gates import evaluate_posting_gate
from waysplit.domain.models import NormalizedBill
from waysplit.errors import ModelEndpointError
from waysplit.model_gateway import (
    DiscoveryResult,
    LocalModel,
    ModelProvider,
    ModelReadiness,
)
from waysplit.repository import Repository, RunRecord
from waysplit.service import WaySplitService
from waysplit.settings import Settings
from waysplit.web import create_app

BROWSER_TOKEN = "synthetic-browser-access-token-0123456789"


@pytest.fixture
def web_client(
    repository: Repository,
    tmp_path: Path,
) -> Iterator[TestClient]:
    settings = Settings(
        data_dir=tmp_path / "app-data",
        max_upload_mib=1,
        model_endpoints=("http://127.0.0.1:11434",),
        allowed_origins=("http://127.0.0.1:9876",),
        browser_access_token=SecretStr(BROWSER_TOKEN),
    )
    app = create_app(settings=settings, repository=repository)
    with TestClient(app) as client:
        _unlock_browser(client)
        yield client


def _unlock_browser(client: TestClient) -> None:
    response = client.post("/unlock", data={"access_token": BROWSER_TOKEN})
    assert response.status_code == 200
    assert client.cookies.get("waysplit_auth")


def _csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/api/session")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    assert client.cookies.get("waysplit_csrf") == token
    return {
        "origin": "http://127.0.0.1:9876",
        "x-csrf-token": token,
    }


def _attest_model(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> dict[str, Any]:
    async def fake_probe(**kwargs: Any) -> ModelReadiness:
        assert kwargs["endpoint"] == "http://127.0.0.1:11434"
        assert kwargs["provider"] is ModelProvider.OLLAMA
        assert kwargs["model"] == "example-local"
        return ModelReadiness(
            endpoint=kwargs["endpoint"],
            provider=kwargs["provider"],
            model=kwargs["model"],
            ready=True,
            structured_output=True,
            vision=False,
            capabilities=("completion",),
            digest="server-observed-model-digest",
            license_excerpt="Synthetic permissive license",
            reason="Synthetic readiness passed.",
        )

    monkeypatch.setattr(web_module, "probe_model", fake_probe)
    response = client.post(
        "/api/models/probe",
        json={
            "endpoint": "http://127.0.0.1:11434/",
            "provider": "ollama",
            "model": "example-local",
        },
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert len(payload["attestation_token"]) >= 20
    assert len(payload["attested_model_digest"]) == 64
    assert payload["attestation_expires_in_seconds"] == 600
    return payload


def _seed_extracted_run(
    repository: Repository,
    bill: NormalizedBill,
    *,
    label: str = "browser-workflow",
) -> RunRecord:
    run = repository.create_run(
        source_sha256=hashlib.sha256(label.encode()).hexdigest(),
        source_name="Example Mobile synthetic statement.pdf",
        source_size=256,
        media_type="application/pdf",
        source_path=f"/synthetic/{label}.pdf",
        model_endpoint="http://127.0.0.1:11434",
        model_provider="ollama",
        model_name="example-local",
        model_digest="synthetic-model-digest",
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
    return repository.get_run(run.id)


def _household_payload(*, complete: bool) -> dict[str, Any]:
    return {
        "participants": [
            {
                "id": "member-alpha",
                "name": "Member Alpha",
                "weight": "1",
                "splitwise_user_id": 101 if complete else None,
            },
            {
                "id": "member-beta",
                "name": "Member Beta",
                "weight": "1",
                "splitwise_user_id": 202 if complete else None,
            },
        ],
        "service_owners": {
            "service-alpha": "member-alpha",
            "service-beta": "member-beta",
        },
        "payer_participant_id": "member-alpha" if complete else None,
        "splitwise_group_id": 44 if complete else None,
    }


def test_browser_entrypoint_and_health_are_local_safe(web_client: TestClient) -> None:
    index = web_client.get("/")
    health = web_client.get("/api/health")

    assert index.status_code == 200
    assert "WaySplit" in index.text
    assert index.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in index.headers["content-security-policy"]
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["audit_chain_valid"] is True
    assert health.json()["local_only_default"] is True
    assert health.headers["cache-control"] == "no-store"


def test_browser_api_is_closed_until_out_of_band_unlock(
    repository: Repository,
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path / "locked-app",
        browser_access_token=SecretStr(BROWSER_TOKEN),
    )
    with TestClient(create_app(settings=settings, repository=repository)) as client:
        locked_page = client.get("/")
        locked_api = client.get("/api/runs")
        wrong_unlock = client.post("/unlock", data={"access_token": "x" * 32})
        _unlock_browser(client)
        unlocked_api = client.get("/api/runs")

    assert locked_page.status_code == 401
    assert "Unlock this browser" in locked_page.text
    assert locked_api.status_code == 401
    assert locked_api.json()["error"] == "browser_locked"
    assert wrong_unlock.status_code == 401
    assert unlocked_api.status_code == 200


def test_unsafe_api_requests_require_matching_csrf_and_allowed_origin(
    web_client: TestClient,
) -> None:
    payload = _household_payload(complete=True)

    missing = web_client.put("/api/household", json=payload)
    headers = _csrf_headers(web_client)
    wrong_origin = web_client.put(
        "/api/household",
        json=payload,
        headers={**headers, "origin": "https://untrusted.example.invalid"},
    )
    wrong_header = web_client.put(
        "/api/household",
        json=payload,
        headers={**headers, "x-csrf-token": "not-the-cookie-token"},
    )

    assert missing.status_code == 403
    assert missing.json()["error"] == "csrf_failed"
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["error"] == "origin_not_allowed"
    assert wrong_header.status_code == 403
    assert wrong_header.json()["error"] == "csrf_failed"


def test_browser_model_discovery_serializes_local_models(
    web_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discovery(
        endpoints: tuple[str, ...],
        *,
        allow_remote: bool,
        timeout_seconds: float = 2.5,
        api_key: str | None = None,
    ) -> tuple[DiscoveryResult, ...]:
        assert endpoints == ("http://127.0.0.1:11434",)
        assert allow_remote is False
        assert timeout_seconds == 2.5
        assert api_key is None
        return (
            DiscoveryResult(
                endpoint=endpoints[0],
                provider=ModelProvider.OLLAMA,
                models=(
                    LocalModel(
                        endpoint=endpoints[0],
                        provider=ModelProvider.OLLAMA,
                        name="example-vision:latest",
                        digest="synthetic-model-digest",
                        vision_hint=True,
                    ),
                ),
            ),
        )

    monkeypatch.setattr(web_module, "discover_models", fake_discovery)

    response = web_client.get("/api/models")

    assert response.status_code == 200
    endpoint = response.json()["endpoints"][0]
    assert endpoint["provider"] == "ollama"
    assert endpoint["models"][0] == {
        "endpoint": "http://127.0.0.1:11434",
        "provider": "ollama",
        "name": "example-vision:latest",
        "size_bytes": None,
        "digest": "synthetic-model-digest",
        "family": None,
        "parameter_size": None,
        "quantization": None,
        "vision_hint": True,
    }


def test_browser_model_discovery_uses_configured_credential(
    repository: Repository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_calls: list[tuple[tuple[str, ...], bool, str | None]] = []

    async def fake_discovery(
        endpoints: tuple[str, ...],
        *,
        allow_remote: bool,
        timeout_seconds: float = 2.5,
        api_key: str | None = None,
    ) -> tuple[DiscoveryResult, ...]:
        assert timeout_seconds == 2.5
        observed_calls.append((endpoints, allow_remote, api_key))
        return ()

    monkeypatch.setattr(web_module, "discover_models", fake_discovery)
    settings = Settings(
        data_dir=tmp_path / "authenticated-discovery-app",
        model_endpoints=("http://127.0.0.1:9090/v1/",),
        model_api_key=SecretStr("configured-model-secret"),
        allowed_origins=("http://127.0.0.1:9876",),
        browser_access_token=SecretStr(BROWSER_TOKEN),
    )

    with TestClient(create_app(settings=settings, repository=repository)) as client:
        _unlock_browser(client)
        response = client.get("/api/models")

    assert response.status_code == 200
    assert observed_calls == [(("http://127.0.0.1:9090/v1",), False, "configured-model-secret")]


def test_probe_rejects_unconfigured_endpoint_before_resolving_model_credential(
    repository: Repository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_calls: list[dict[str, Any]] = []

    async def forbidden_probe(**kwargs: Any) -> ModelReadiness:
        probe_calls.append(kwargs)
        raise AssertionError("unconfigured endpoint must not receive a request")

    monkeypatch.setattr(web_module, "probe_model", forbidden_probe)
    settings = Settings(
        data_dir=tmp_path / "credential-safe-app",
        model_endpoints=("http://127.0.0.1:11434",),
        model_api_key=SecretStr("configured-model-secret"),
        allowed_origins=("http://127.0.0.1:9876",),
        browser_access_token=SecretStr(BROWSER_TOKEN),
    )
    with TestClient(create_app(settings=settings, repository=repository)) as client:
        _unlock_browser(client)
        headers = _csrf_headers(client)
        response = client.post(
            "/api/models/probe",
            json={
                "endpoint": "http://127.0.0.1:9090",
                "provider": "openai_compatible",
                "model": "unconfigured-model",
            },
            headers=headers,
        )

    assert response.status_code == 422
    assert response.json()["error"] == "modelendpoint"
    assert "allowlist" in response.json()["message"]
    assert probe_calls == []


def test_upload_attestation_is_bound_to_exact_model_before_file_storage(
    web_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = _csrf_headers(web_client)
    attestation = _attest_model(web_client, monkeypatch, headers)

    async def forbidden_store(*args: object, **kwargs: object) -> object:
        raise AssertionError("a mismatched attestation must be rejected before file storage")

    monkeypatch.setattr(web_module, "_store_upload", forbidden_store)

    response = web_client.post(
        "/api/runs",
        data={
            "endpoint": "http://127.0.0.1:11434",
            "provider": "ollama",
            "model": "different-model",
            "probe_attestation": attestation["attestation_token"],
        },
        files={"statement": ("private.pdf", b"%PDF-1.7\nprivate", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"] == "modelendpoint"
    assert "does not match" in response.json()["message"]


def test_probe_attestations_expire_server_side(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(web_module.time, "monotonic", lambda: clock[0])
    store = web_module.ProbeAttestationStore(ttl_seconds=5, maximum_entries=2)
    readiness = ModelReadiness(
        endpoint="http://127.0.0.1:11434",
        provider=ModelProvider.OLLAMA,
        model="example-local",
        ready=True,
        structured_output=True,
        vision=False,
        capabilities=("completion",),
        digest="server-digest",
        license_excerpt=None,
        reason="passed",
    )
    token, _digest = store.issue(readiness, session_token="session-alpha")
    clock[0] = 106.0

    with pytest.raises(ModelEndpointError, match="readiness test again"):
        store.require(
            token,
            endpoint=readiness.endpoint,
            provider=readiness.provider,
            model=readiness.model,
            session_token="session-alpha",
        )


def test_splitwise_context_requires_terms_and_returns_minimized_account_data(
    web_client: TestClient,
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_tokens: list[str] = []

    class FakeSplitwiseClient:
        def __init__(self, *, access_token: str) -> None:
            captured_tokens.append(access_token)

        async def account_context(self) -> SplitwiseContext:
            return SplitwiseContext(
                current_user=SplitwiseMember(user_id=101, display_name="Member Alpha"),
                groups=(
                    SplitwiseGroup(
                        group_id=44,
                        name="Synthetic household",
                        members=(SplitwiseMember(user_id=101, display_name="Member Alpha"),),
                    ),
                ),
            )

    monkeypatch.setattr(web_module, "SplitwiseClient", FakeSplitwiseClient)
    headers = _csrf_headers(web_client)

    refused = web_client.post(
        "/api/splitwise/context",
        json={
            "access_token": "synthetic-access-token",
            "accepted_destination_terms": False,
        },
        headers=headers,
    )
    connected = web_client.post(
        "/api/splitwise/context",
        json={
            "access_token": "synthetic-access-token",
            "accepted_destination_terms": True,
        },
        headers=headers,
    )

    assert refused.status_code == 409
    assert refused.json()["error"] == "posting_blocked"
    assert connected.status_code == 200
    assert connected.json() == {
        "current_user": {"user_id": 101, "display_name": "Member Alpha"},
        "groups": [
            {
                "group_id": 44,
                "name": "Synthetic household",
                "members": [{"user_id": 101, "display_name": "Member Alpha"}],
            }
        ],
    }
    assert captured_tokens == ["synthetic-access-token"]
    consent_entries = repository.audit.entries()
    assert len(consent_entries) == 1
    assert consent_entries[0].event_type == "destination.consent_acknowledged"
    assert consent_entries[0].payload["action"] == "read_account_context"
    assert "synthetic-access-token" not in str(consent_entries[0].payload)
    assert "synthetic-access-token" not in repository.database_path.read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_browser_upload_accepts_magic_valid_statement_and_rejects_duplicate(
    web_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processed: list[str] = []

    async def do_not_extract(self: WaySplitService, run_id: str) -> None:
        processed.append(run_id)

    monkeypatch.setattr(WaySplitService, "process_run", do_not_extract)
    headers = _csrf_headers(web_client)
    attestation = _attest_model(web_client, monkeypatch, headers)
    fields = {
        "endpoint": "http://127.0.0.1:11434",
        "provider": "ollama",
        "model": "example-local",
        "model_digest": "browser-forged-digest-must-be-ignored",
        "probe_attestation": attestation["attestation_token"],
    }
    statement = b"%PDF-1.7\nExample Mobile synthetic upload"

    created = web_client.post(
        "/api/runs",
        data=fields,
        files={"statement": ("example-statement.pdf", statement, "application/octet-stream")},
        headers=headers,
    )
    duplicate = web_client.post(
        "/api/runs",
        data=fields,
        files={"statement": ("renamed.pdf", statement, "application/pdf")},
        headers=headers,
    )

    assert created.status_code == 202
    assert created.json()["run"]["media_type"] == "application/pdf"
    assert created.json()["run"]["status"] == "queued"
    assert created.json()["run"]["model"]["digest"] == attestation["attested_model_digest"]
    assert processed == [created.json()["run"]["id"]]
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "duplicate_statement"
    assert duplicate.json()["existing_run_id"] == created.json()["run"]["id"]


def test_browser_upload_limit_and_magic_fail_closed(
    web_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = _csrf_headers(web_client)
    attestation = _attest_model(web_client, monkeypatch, headers)
    fields = {
        "endpoint": "http://127.0.0.1:11434",
        "provider": "ollama",
        "model": "example-local",
        "probe_attestation": attestation["attestation_token"],
    }
    oversized = b"%PDF-1.7\n" + b"x" * (1024 * 1024)

    too_large = web_client.post(
        "/api/runs",
        data=fields,
        files={"statement": ("too-large.pdf", oversized, "application/pdf")},
        headers=headers,
    )
    invalid_magic = web_client.post(
        "/api/runs",
        data=fields,
        files={"statement": ("masquerading.pdf", b"not a PDF", "application/pdf")},
        headers=headers,
    )

    assert too_large.status_code == 409
    assert too_large.json()["error"] == "posting_blocked"
    assert invalid_magic.status_code == 422
    assert invalid_magic.json()["error"] == "document"


def test_review_and_preview_gates_block_then_allow_confirmation(
    web_client: TestClient,
    repository: Repository,
    normalized_bill: NormalizedBill,
    normalized_bill_payload: dict[str, object],
) -> None:
    run = _seed_extracted_run(repository, normalized_bill)
    headers = _csrf_headers(web_client)
    unreconciled_payload = dict(normalized_bill_payload)
    unreconciled_payload["totals"] = {
        **normalized_bill_payload["totals"],  # type: ignore[dict-item]
        "current_charges": "46.00",
        "amount_due": "46.00",
    }

    blocked_review = web_client.put(
        f"/api/runs/{run.id}/bill",
        json=unreconciled_payload,
        headers=headers,
    )
    passing_review = web_client.put(
        f"/api/runs/{run.id}/bill",
        json=normalized_bill_payload,
        headers=headers,
    )
    blocked_preview = web_client.post(
        f"/api/runs/{run.id}/preview",
        json=_household_payload(complete=False),
        headers=headers,
    )
    premature_confirmation = web_client.post(
        f"/api/runs/{run.id}/confirmation",
        headers=headers,
    )
    passing_preview = web_client.post(
        f"/api/runs/{run.id}/preview",
        json=_household_payload(complete=True),
        headers=headers,
    )
    confirmation = web_client.post(
        f"/api/runs/{run.id}/confirmation",
        headers=headers,
    )

    assert blocked_review.status_code == 200
    assert blocked_review.json()["run"]["status"] == "blocked"
    assert blocked_review.json()["run"]["gate"]["status"] == "blocked"
    assert passing_review.status_code == 200
    assert passing_review.json()["run"]["status"] == "needs_review"

    assert blocked_preview.status_code == 200
    assert blocked_preview.json()["run"]["status"] == "blocked"
    assert len(blocked_preview.json()["run"]["gate"]["destination_blockers"]) == 4
    assert premature_confirmation.status_code == 422
    assert premature_confirmation.json()["error"] == "confirmation"

    assert passing_preview.status_code == 200
    assert passing_preview.json()["run"]["status"] == "ready"
    assert passing_preview.json()["run"]["preview"]["blockers"] == []
    assert len(passing_preview.json()["run"]["preview_digest"]) == 64
    assert confirmation.status_code == 200
    assert len(confirmation.json()["confirmation_token"]) >= 20


def test_post_route_requires_both_human_acknowledgements_before_side_effects(
    web_client: TestClient,
    repository: Repository,
    normalized_bill: NormalizedBill,
) -> None:
    run = _seed_extracted_run(repository, normalized_bill, label="post-gate")
    headers = _csrf_headers(web_client)
    preview = web_client.post(
        f"/api/runs/{run.id}/preview",
        json=_household_payload(complete=True),
        headers=headers,
    )
    token = web_client.post(
        f"/api/runs/{run.id}/confirmation",
        headers=headers,
    ).json()["confirmation_token"]

    response = web_client.post(
        f"/api/runs/{run.id}/post",
        json={
            "confirmation_token": token,
            "access_token": "synthetic-access-token",
            "acknowledged_preview": False,
            "accepted_destination_terms": True,
        },
        headers=headers,
    )

    assert preview.status_code == 200
    assert response.status_code == 409
    assert response.json()["error"] == "posting_blocked"
    assert repository.posting_for_run(run.id) is None
    assert repository.get_run(run.id).status == "ready"
