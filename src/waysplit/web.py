from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlsplit

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    Request,
    UploadFile,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from waysplit import __version__
from waysplit.destinations.splitwise import AmbiguousDestinationError, SplitwiseClient
from waysplit.domain.models import NormalizedBill
from waysplit.errors import (
    DuplicateStatementError,
    ModelEndpointError,
    PostingBlockedError,
    WaySplitError,
)
from waysplit.household import HouseholdConfig
from waysplit.ingest import inspect_media_type
from waysplit.model_gateway import (
    ModelProvider,
    ModelReadiness,
    discover_models,
    probe_model,
    readiness_attestation_digest,
    require_configured_endpoint,
)
from waysplit.repository import PostingRecord, Repository, RunRecord
from waysplit.service import WaySplitService
from waysplit.settings import Settings, load_settings

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=PACKAGE_ROOT / "templates")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PROBE_ATTESTATION_TTL_SECONDS = 10 * 60
MAX_PROBE_ATTESTATIONS = 128


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ModelProbeRequest(ApiModel):
    endpoint: str = Field(min_length=1, max_length=500)
    provider: ModelProvider
    model: str = Field(min_length=1, max_length=300)


class PostRequest(ApiModel):
    confirmation_token: str = Field(min_length=20, max_length=200)
    access_token: str | None = Field(default=None, max_length=1000)
    acknowledged_preview: bool
    accepted_destination_terms: bool


class SplitwiseConnectRequest(ApiModel):
    access_token: str | None = Field(default=None, max_length=1000)
    accepted_destination_terms: bool


class RollbackRequest(ApiModel):
    confirmation_token: str = Field(min_length=20, max_length=200)
    access_token: str | None = Field(default=None, max_length=1000)
    confirmation_phrase: str = Field(max_length=20)
    acknowledged_target: bool


@dataclass(frozen=True, slots=True)
class _ProbeAttestation:
    endpoint: str
    provider: ModelProvider
    model: str
    model_digest: str
    session_digest: str
    expires_at: float


class ProbeAttestationStore:
    """Bounded in-memory proof that a server-run readiness check passed recently."""

    def __init__(
        self,
        *,
        ttl_seconds: int = PROBE_ATTESTATION_TTL_SECONDS,
        maximum_entries: int = MAX_PROBE_ATTESTATIONS,
    ) -> None:
        if ttl_seconds < 1 or maximum_entries < 1:
            raise ValueError("probe attestation limits must be positive")
        self.ttl_seconds = ttl_seconds
        self.maximum_entries = maximum_entries
        self._records: dict[str, _ProbeAttestation] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        readiness: ModelReadiness,
        *,
        session_token: str,
    ) -> tuple[str, str]:
        if not readiness.ready:
            raise ModelEndpointError("Only a successful readiness probe can be attested.")
        model_digest = readiness_attestation_digest(readiness)
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        record = _ProbeAttestation(
            endpoint=readiness.endpoint,
            provider=readiness.provider,
            model=readiness.model,
            model_digest=model_digest,
            session_digest=_session_digest(session_token),
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._remove_expired(now)
            while len(self._records) >= self.maximum_entries:
                oldest_token = min(
                    self._records,
                    key=lambda item: self._records[item].expires_at,
                )
                self._records.pop(oldest_token, None)
            self._records[token] = record
        return token, model_digest

    def require(
        self,
        token: str,
        *,
        endpoint: str,
        provider: ModelProvider,
        model: str,
        session_token: str,
    ) -> str:
        now = time.monotonic()
        with self._lock:
            self._remove_expired(now)
            record = self._records.get(token)
        if record is None:
            raise ModelEndpointError(
                "Run the selected model's readiness test again before uploading."
            )
        if (
            record.endpoint != endpoint
            or record.provider is not provider
            or record.model != model
            or not secrets.compare_digest(
                record.session_digest,
                _session_digest(session_token),
            )
        ):
            raise ModelEndpointError(
                "The readiness attestation does not match this endpoint and model."
            )
        return record.model_digest

    def _remove_expired(self, now: float) -> None:
        expired = [token for token, record in self._records.items() if record.expires_at <= now]
        for token in expired:
            self._records.pop(token, None)


def _session_digest(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def create_app(
    *,
    settings: Settings | None = None,
    repository: Repository | None = None,
) -> FastAPI:
    runtime_settings = settings or load_settings()
    owns_repository = repository is None
    probe_attestations = ProbeAttestationStore()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime_settings.ensure_directories()
        repo = repository or Repository(runtime_settings.database_path)
        app.state.settings = runtime_settings
        app.state.repository = repo
        app.state.service = WaySplitService(settings=runtime_settings, repository=repo)
        for temporary_path in repo.recover_interrupted_runs():
            await asyncio.to_thread(Path(temporary_path).unlink, missing_ok=True)
        repo.recover_interrupted_postings()
        yield
        if owns_repository:
            repo.close()

    app = FastAPI(
        title="WaySplit",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    allowed_hosts = {"127.0.0.1", "localhost", "testserver"}
    for origin in runtime_settings.allowed_origins:
        hostname = urlsplit(origin).hostname
        if hostname:
            allowed_hosts.add(hostname)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(allowed_hosts))
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")

    @app.middleware("http")
    async def browser_safety(request: Request, call_next: Any) -> Any:
        if request.method == "POST" and request.url.path == "/api/runs":
            raw_length = request.headers.get("content-length")
            try:
                content_length = int(raw_length) if raw_length is not None else None
            except ValueError:
                content_length = -1
            multipart_allowance = 1024 * 1024
            if content_length is None:
                return JSONResponse(
                    status_code=status.HTTP_411_LENGTH_REQUIRED,
                    content={
                        "error": "content_length_required",
                        "message": "Statement uploads require a Content-Length header.",
                    },
                )
            if content_length < 0 or content_length > (
                runtime_settings.max_upload_bytes + multipart_allowance
            ):
                return JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={
                        "error": "upload_too_large",
                        "message": (
                            f"Statement uploads are limited to "
                            f"{runtime_settings.max_upload_mib} MiB."
                        ),
                    },
                )
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            allowed = {value.rstrip("/") for value in runtime_settings.allowed_origins}
            if origin and origin.rstrip("/") not in allowed:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": "origin_not_allowed",
                        "message": "Request origin is not allowed.",
                    },
                )
            cookie_token = request.cookies.get("waysplit_csrf")
            header_token = request.headers.get("x-csrf-token")
            if (
                not cookie_token
                or not header_token
                or not secrets.compare_digest(cookie_token, header_token)
            ):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": "csrf_failed",
                        "message": "Refresh WaySplit and try the action again.",
                    },
                )

        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        issues = [
            {
                "location": [str(part) for part in error.get("loc", ())],
                "message": error.get("msg", "Invalid value"),
                "type": error.get("type", "validation_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": "validation_failed",
                "message": "One or more fields are invalid.",
                "issues": issues,
            },
        )

    @app.exception_handler(DuplicateStatementError)
    async def duplicate_handler(_request: Request, exc: DuplicateStatementError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": "duplicate_statement",
                "message": str(exc),
                "existing_run_id": exc.run_id,
            },
        )

    @app.exception_handler(AmbiguousDestinationError)
    async def ambiguous_handler(_request: Request, exc: AmbiguousDestinationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "destination_ambiguous", "message": str(exc)},
        )

    @app.exception_handler(PostingBlockedError)
    async def posting_blocked_handler(_request: Request, exc: PostingBlockedError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "posting_blocked", "message": str(exc)},
        )

    @app.exception_handler(WaySplitError)
    async def waysplit_error_handler(_request: Request, exc: WaySplitError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": type(exc).__name__.removesuffix("Error").lower(),
                "message": str(exc),
            },
        )

    @app.exception_handler(KeyError)
    async def missing_handler(_request: Request, _exc: KeyError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "not_found", "message": "The requested record was not found."},
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        csrf_token = request.cookies.get("waysplit_csrf") or secrets.token_urlsafe(32)
        response = TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={"csrf_token": csrf_token, "version": __version__},
        )
        response.set_cookie(
            "waysplit_csrf",
            csrf_token,
            httponly=False,
            secure=False,
            samesite="strict",
            max_age=8 * 60 * 60,
            path="/",
        )
        return response

    @app.get("/privacy", response_class=HTMLResponse)
    async def privacy(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="privacy.html",
            context={"version": __version__},
        )

    @app.get("/api/session")
    async def session(request: Request) -> JSONResponse:
        csrf_token = request.cookies.get("waysplit_csrf") or secrets.token_urlsafe(32)
        response = JSONResponse({"csrf_token": csrf_token})
        response.set_cookie(
            "waysplit_csrf",
            csrf_token,
            httponly=False,
            secure=False,
            samesite="strict",
            max_age=8 * 60 * 60,
            path="/",
        )
        return response

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        repo = _repo(request)
        audit = repo.audit.verify()
        database_valid = repo.integrity_check()
        return {
            "status": "ok" if audit.valid and database_valid else "degraded",
            "version": __version__,
            "audit_chain_valid": audit.valid,
            "database_integrity_valid": database_valid,
            "local_only_default": not runtime_settings.allow_remote_model_endpoints,
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        api_key = (
            runtime_settings.model_api_key.get_secret_value()
            if runtime_settings.model_api_key
            else None
        )
        results = await discover_models(
            runtime_settings.model_endpoints,
            allow_remote=runtime_settings.allow_remote_model_endpoints,
            api_key=api_key,
        )
        return {"endpoints": jsonable_encoder([asdict(result) for result in results])}

    @app.post("/api/models/probe")
    async def model_probe(request: Request, payload: ModelProbeRequest) -> dict[str, Any]:
        normalized_endpoint = require_configured_endpoint(
            payload.endpoint,
            configured_endpoints=runtime_settings.model_endpoints,
            allow_remote=runtime_settings.allow_remote_model_endpoints,
        )
        # The configured credential is resolved only after the endpoint passes the
        # exact normalized allowlist check.
        api_key = (
            runtime_settings.model_api_key.get_secret_value()
            if runtime_settings.model_api_key
            else None
        )
        result = await probe_model(
            endpoint=normalized_endpoint,
            provider=payload.provider,
            model=payload.model,
            allow_remote=runtime_settings.allow_remote_model_endpoints,
            timeout_seconds=runtime_settings.model_timeout_seconds,
            api_key=api_key,
        )
        response = cast(dict[str, Any], jsonable_encoder(asdict(result)))
        response["attestation_token"] = None
        response["attested_model_digest"] = None
        response["attestation_expires_in_seconds"] = None
        if result.ready:
            token, attested_digest = probe_attestations.issue(
                result,
                session_token=_csrf_session_token(request),
            )
            response["attestation_token"] = token
            response["attested_model_digest"] = attested_digest
            response["attestation_expires_in_seconds"] = probe_attestations.ttl_seconds
        return response

    @app.get("/api/household")
    async def get_household(request: Request) -> dict[str, Any]:
        config = _repo(request).get_household()
        return {"household": config}

    @app.put("/api/household")
    async def put_household(request: Request, config: HouseholdConfig) -> dict[str, Any]:
        _repo(request).save_household(config.json_safe())
        return {"household": config.json_safe()}

    @app.post("/api/splitwise/context")
    async def splitwise_context(
        request: Request, payload: SplitwiseConnectRequest
    ) -> dict[str, Any]:
        if not payload.accepted_destination_terms:
            raise PostingBlockedError(
                "Review and accept the destination provider's current terms before connecting."
            )
        token = payload.access_token.strip() if payload.access_token else ""
        if not token:
            raise PostingBlockedError("Enter a Splitwise access token to read groups and members.")
        _repo(request).audit.append(
            "destination.consent_acknowledged",
            {
                "action": "read_account_context",
                "app_version": __version__,
                "destination": "splitwise",
                "participant_consent_asserted": True,
                "privacy_policy": "/privacy",
                "terms_url": "https://dev.splitwise.com/",
            },
        )
        context = await SplitwiseClient(access_token=token).account_context()
        return cast(dict[str, Any], jsonable_encoder(asdict(context)))

    @app.post("/api/runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_run(
        request: Request,
        background_tasks: BackgroundTasks,
        statement: Annotated[UploadFile, File()],
        endpoint: Annotated[str, Form(min_length=1, max_length=500)],
        provider: Annotated[ModelProvider, Form()],
        model: Annotated[str, Form(min_length=1, max_length=300)],
        probe_attestation: Annotated[str, Form(min_length=20, max_length=200)],
    ) -> dict[str, Any]:
        normalized_endpoint = require_configured_endpoint(
            endpoint,
            configured_endpoints=runtime_settings.model_endpoints,
            allow_remote=runtime_settings.allow_remote_model_endpoints,
        )
        model_digest = probe_attestations.require(
            probe_attestation,
            endpoint=normalized_endpoint,
            provider=provider,
            model=model,
            session_token=_csrf_session_token(request),
        )
        path, source_hash, source_size, media_type, source_name = await _store_upload(
            statement,
            upload_dir=runtime_settings.upload_dir,
            max_bytes=runtime_settings.max_upload_bytes,
        )
        try:
            run = _repo(request).create_run(
                source_sha256=source_hash,
                source_name=source_name,
                source_size=source_size,
                media_type=media_type,
                source_path=str(path),
                model_endpoint=normalized_endpoint,
                model_provider=provider.value,
                model_name=model,
                model_digest=model_digest,
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        background_tasks.add_task(_service(request).process_run, run.id)
        return {"run": _run_json(_repo(request), run)}

    @app.get("/api/runs")
    async def list_runs(request: Request) -> dict[str, Any]:
        repo = _repo(request)
        return {"runs": [_run_json(repo, run) for run in repo.list_runs()]}

    @app.get("/api/runs/{run_id}")
    async def get_run(request: Request, run_id: str) -> dict[str, Any]:
        repo = _repo(request)
        return {"run": _run_json(repo, repo.get_run(run_id))}

    @app.put("/api/runs/{run_id}/bill")
    async def review_bill(request: Request, run_id: str, bill: NormalizedBill) -> dict[str, Any]:
        run = _service(request).review_bill(run_id, bill)
        return {"run": _run_json(_repo(request), run)}

    @app.post("/api/runs/{run_id}/preview")
    async def create_preview(
        request: Request, run_id: str, household: HouseholdConfig
    ) -> dict[str, Any]:
        run = _service(request).create_preview(run_id, household)
        return {"run": _run_json(_repo(request), run)}

    @app.post("/api/runs/{run_id}/confirmation")
    async def confirmation(request: Request, run_id: str) -> dict[str, Any]:
        confirmation_token = _service(request).issue_confirmation(run_id)
        target = _service(request).confirmation_target(run_id)
        return {
            "confirmation_token": confirmation_token,
            "target": target,
        }

    @app.post("/api/runs/{run_id}/post")
    async def post_run(request: Request, run_id: str, payload: PostRequest) -> dict[str, Any]:
        posting = await _service(request).post_to_splitwise(
            run_id,
            confirmation_token=payload.confirmation_token,
            access_token=payload.access_token,
            acknowledged_preview=payload.acknowledged_preview,
            accepted_destination_terms=payload.accepted_destination_terms,
        )
        return {"posting": _posting_json(posting)}

    @app.post("/api/runs/{run_id}/rollback")
    async def rollback_run(
        request: Request, run_id: str, payload: RollbackRequest
    ) -> dict[str, Any]:
        posting = await _service(request).rollback_splitwise(
            run_id,
            confirmation_token=payload.confirmation_token,
            access_token=payload.access_token,
            confirmation_phrase=payload.confirmation_phrase,
            acknowledged_target=payload.acknowledged_target,
        )
        return {"posting": _posting_json(posting)}

    @app.post("/api/runs/{run_id}/rollback-confirmation")
    async def rollback_confirmation(request: Request, run_id: str) -> dict[str, Any]:
        confirmation_token = _service(request).issue_rollback_confirmation(run_id)
        target = _service(request).rollback_target(run_id)
        return {
            "confirmation_token": confirmation_token,
            "target": target,
        }

    @app.get("/api/audit/verify")
    async def audit_verify(request: Request) -> dict[str, Any]:
        return _service(request).audit_status()

    return app


async def _store_upload(
    upload: UploadFile,
    *,
    upload_dir: Path,
    max_bytes: int,
) -> tuple[Path, str, int, str, str]:
    await asyncio.to_thread(upload_dir.mkdir, mode=0o700, parents=True, exist_ok=True)
    path = upload_dir / f"{secrets.token_hex(16)}.upload"
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as target:
            path.chmod(0o600)
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise PostingBlockedError(
                        f"The statement exceeds the {max_bytes // (1024 * 1024)} MiB limit."
                    )
                digest.update(chunk)
                target.write(chunk)
            target.flush()
        if size == 0:
            raise PostingBlockedError("The uploaded statement is empty.")
        media_type = inspect_media_type(path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    raw_name = Path(upload.filename or "statement").name
    safe_name = "".join(character for character in raw_name if character.isprintable())[:180]
    return path, digest.hexdigest(), size, media_type, safe_name or "statement"


def _run_json(repository: Repository, run: RunRecord) -> dict[str, Any]:
    posting = repository.posting_for_run(run.id)
    return {
        "id": run.id,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "status": run.status,
        "source_sha256": run.source_sha256,
        "source_name": run.source_name,
        "source_size": run.source_size,
        "media_type": run.media_type,
        "model": {
            "endpoint": run.model_endpoint,
            "provider": run.model_provider,
            "name": run.model_name,
            "digest": run.model_digest,
        },
        "ingestion_warnings": run.ingestion_warnings,
        "bill": run.bill.model_dump(mode="json") if run.bill else None,
        "reconciliation": run.reconciliation,
        "gate": run.gate,
        "allocation": run.allocation,
        "household": run.household,
        "preview": run.preview,
        "preview_digest": run.preview_digest,
        "logical_fingerprint": run.logical_fingerprint,
        "error": (
            {"code": run.error_code, "message": run.error_message}
            if run.error_code or run.error_message
            else None
        ),
        "posting": _posting_json(posting) if posting else None,
    }


def _posting_json(posting: PostingRecord) -> dict[str, Any]:
    return {
        "id": posting.id,
        "destination": posting.destination,
        "status": posting.status,
        "correlation_id": posting.correlation_id,
        "external_id": posting.external_id,
        "created_at": posting.created_at,
        "updated_at": posting.updated_at,
        "response_summary": posting.response_summary,
    }


def _repo(request: Request) -> Repository:
    return cast(Repository, request.app.state.repository)


def _service(request: Request) -> WaySplitService:
    return cast(WaySplitService, request.app.state.service)


def _csrf_session_token(request: Request) -> str:
    token = request.cookies.get("waysplit_csrf")
    if not token:
        raise ModelEndpointError("The browser safety session is unavailable. Refresh and retry.")
    return token
