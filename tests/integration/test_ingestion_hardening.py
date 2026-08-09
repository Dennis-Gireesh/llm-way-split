from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image
from pydantic import SecretStr
from pypdf import PdfWriter

import waysplit.ingest as ingest_module
import waysplit.ingest_worker as worker_module
import waysplit.service as service_module
from waysplit.domain.models import NormalizedBill
from waysplit.errors import DocumentError
from waysplit.ingest import (
    DocumentContent,
    DocumentPage,
    IngestionLimits,
    _data_uri_length,
    _extract_native_page_text,
    _IngestionBudget,
    extract_document,
)
from waysplit.ingest_worker import extract_document_isolated
from waysplit.model_gateway import (
    ModelProvider,
    ModelReadiness,
    readiness_attestation_digest,
)
from waysplit.repository import Repository
from waysplit.service import WaySplitService
from waysplit.settings import Settings

SYNTHETIC_PDF = (
    Path(__file__).resolve().parents[2] / "examples" / "synthetic" / "example-mobile-statement.pdf"
)


def _limits(
    *,
    pixels: int = 1_000_000,
    image_bytes: int = 1_000_000,
    image_characters: int = 2_000_000,
    text_characters: int = 10_000,
) -> IngestionLimits:
    return IngestionLimits(
        max_total_render_pixels=pixels,
        max_image_bytes=image_bytes,
        max_image_characters=image_characters,
        max_text_characters=text_characters,
    )


def test_cumulative_rendered_pixel_budget_fails_closed_before_next_page() -> None:
    budget = _IngestionBudget(_limits(pixels=100))

    budget.reserve_render_pixels(60)

    with pytest.raises(DocumentError, match="cumulative raster"):
        budget.reserve_render_pixels(41)
    assert budget.rendered_pixels == 60


def test_cumulative_encoded_image_byte_budget_is_not_per_page() -> None:
    budget = _IngestionBudget(_limits(image_bytes=7))

    budget.reserve_image(b"abcd", "image/png")

    with pytest.raises(DocumentError, match="byte limit"):
        budget.reserve_image(b"efgh", "image/png")


def test_encoded_image_character_budget_counts_base64_and_data_uri_prefix() -> None:
    encoded_length = _data_uri_length(3, "image/png")
    budget = _IngestionBudget(_limits(image_characters=encoded_length - 1))

    with pytest.raises(DocumentError, match="character limit"):
        budget.reserve_image(b"abc", "image/png")


def test_text_budget_matches_serialized_document_text_across_pages() -> None:
    first = "first page"
    second = "second page"
    document = DocumentContent(
        media_type="application/pdf",
        pages=(DocumentPage(1, first), DocumentPage(2, second)),
    )
    budget = _IngestionBudget(_limits(text_characters=len(document.text)))

    budget.reserve_text(first, page_number=1)
    budget.reserve_text(second, page_number=2)

    assert budget.text_characters == len(document.text)
    with pytest.raises(DocumentError, match="text exceeds"):
        budget.reserve_text("x", page_number=3)


def test_native_pdf_text_limit_is_enforced_before_document_accumulation() -> None:
    with pytest.raises(DocumentError, match="text exceeds"):
        extract_document(
            SYNTHETIC_PDF,
            max_pages=24,
            limits=_limits(text_characters=100),
        )


def test_native_pdf_fragment_guard_stops_parser_before_full_text_accumulates() -> None:
    class ChunkedPage:
        def __init__(self) -> None:
            self.fragments_attempted = 0

        def extract_text(self, *args: object, **kwargs: object) -> str:
            del args
            visitor = kwargs["visitor_text"]
            assert callable(visitor)
            output: list[str] = []
            for fragment in ("a" * 30, "b" * 30, "never reached"):
                self.fragments_attempted += 1
                visitor(fragment, None, None, None, None)
                output.append(fragment)
            return "".join(output)

    page = ChunkedPage()
    prefix_length = len("--- PAGE 1 ---\n")
    budget = _IngestionBudget(_limits(text_characters=prefix_length + 50))

    with pytest.raises(DocumentError, match="text exceeds"):
        _extract_native_page_text(page, budget=budget, page_number=1)

    assert page.fragments_attempted == 2


def test_pdf_production_path_invokes_native_fragment_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_native_text(
        page: object,
        *,
        budget: object,
        page_number: int,
    ) -> str:
        raise DocumentError("fragment guard reached")

    monkeypatch.setattr(ingest_module, "_extract_native_page_text", reject_native_text)

    with pytest.raises(DocumentError, match="fragment guard reached"):
        extract_document(SYNTHETIC_PDF, max_pages=24)


def test_pdf_cumulative_pixel_limit_is_reserved_before_rendering_next_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement = tmp_path / "two-blank-pages.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    with statement.open("wb") as output:
        writer.write(output)
    rendered_pages: list[int] = []

    def fake_render(
        _document: object,
        index: int,
        *,
        budget: object,
        reserved_pixels: int,
    ) -> bytes:
        rendered_pages.append(index)
        return b"small-render"

    def fake_ocr(
        _data: bytes,
        *,
        page_number: int,
        warnings_out: list[str],
    ) -> str:
        return ""

    monkeypatch.setattr(ingest_module, "_render_pdf_page", fake_render)
    monkeypatch.setattr(ingest_module, "_run_ocr", fake_ocr)

    with pytest.raises(DocumentError, match="cumulative raster"):
        extract_document(
            statement,
            max_pages=2,
            limits=_limits(pixels=30_000),
        )

    assert rendered_pages == [0]


def test_image_encoding_budget_is_checked_before_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "statement.png"
    Image.new("RGB", (32, 32), color="white").save(image_path)

    def unexpected_ocr(
        data: bytes,
        *,
        page_number: int,
        warnings_out: list[str],
    ) -> str:
        raise AssertionError("OCR must not run after an encoding budget failure")

    monkeypatch.setattr(ingest_module, "_run_ocr", unexpected_ocr)

    with pytest.raises(DocumentError, match="byte limit"):
        extract_document(
            image_path,
            max_pages=1,
            limits=_limits(image_bytes=1),
        )


def test_isolated_worker_extracts_synthetic_pdf() -> None:
    document = extract_document_isolated(SYNTHETIC_PDF, max_pages=24)

    assert document.media_type == "application/pdf"
    assert len(document.pages) == 1
    assert "AMOUNT DUE" in document.text


def test_isolated_worker_can_render_and_invoke_ocr_with_minimal_environment(
    tmp_path: Path,
) -> None:
    statement = tmp_path / "blank-page.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with statement.open("wb") as output:
        writer.write(output)

    document = extract_document_isolated(statement, max_pages=1)

    assert len(document.pages) == 1
    assert document.pages[0].image_data_uri is not None
    assert document.pages[0].image_data_uri.startswith("data:image/png;base64,")


def test_isolated_worker_propagates_only_safe_document_errors(tmp_path: Path) -> None:
    invalid = tmp_path / "private-customer-name.pdf"
    invalid.write_bytes(b"not a statement")

    with pytest.raises(DocumentError, match="PDF, PNG, JPEG, TIFF, or WebP") as captured:
        extract_document_isolated(invalid, max_pages=24)

    assert str(invalid) not in str(captured.value)


def test_isolated_worker_enforces_wall_clock_timeout() -> None:
    with pytest.raises(DocumentError, match="wall-clock safety limit"):
        extract_document_isolated(
            SYNTHETIC_PDF,
            max_pages=24,
            timeout_seconds=0.001,
        )


def test_actual_worker_scrubs_application_secrets_proxies_and_unrelated_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(SYNTHETIC_PDF.read_bytes())
    for name, value in {
        "WAYSPLIT_MODEL_API_KEY": "model-secret-must-not-enter-worker",
        "WAYSPLIT_SPLITWISE_ACCESS_TOKEN": "splitwise-secret-must-not-enter-worker",
        "OPENAI_API_KEY": "generic-secret-must-not-enter-worker",
        "HTTPS_PROXY": "http://credential@proxy.invalid",
        "HTTP_PROXY": "http://credential@proxy.invalid",
        "ALL_PROXY": "socks5://credential@proxy.invalid",
        "TESSDATA_PREFIX": "/synthetic/tessdata",
    }.items():
        monkeypatch.setenv(name, value)

    original_get_context = worker_module.multiprocessing.get_context
    monkeypatch.setattr(
        worker_module.multiprocessing,
        "get_context",
        lambda _method: original_get_context("fork"),
    )

    def inspect_child(
        path: Path,
        *,
        max_pages: int,
        limits: IngestionLimits,
    ) -> DocumentContent:
        del path, max_pages, limits
        active_descriptors: list[int] = []
        for descriptor in worker_module._open_file_descriptors():
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            active_descriptors.append(descriptor)
        result = {
            "secret_presence": {
                name: name in os.environ
                for name in (
                    "WAYSPLIT_MODEL_API_KEY",
                    "WAYSPLIT_SPLITWISE_ACCESS_TOKEN",
                    "OPENAI_API_KEY",
                )
            },
            "proxy_presence": {
                name: name in os.environ for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
            },
            "environment_keys": sorted(os.environ),
            "non_standard_descriptor_count": len(
                [descriptor for descriptor in active_descriptors if descriptor > 2]
            ),
        }
        return DocumentContent(
            media_type="application/pdf",
            pages=(DocumentPage(number=1, text=json.dumps(result, sort_keys=True)),),
        )

    monkeypatch.setattr(worker_module, "extract_document", inspect_child)

    document = extract_document_isolated(source, max_pages=24)
    diagnostics = json.loads(document.pages[0].text)

    assert not any(diagnostics["secret_presence"].values())
    assert not any(diagnostics["proxy_presence"].values())
    assert set(diagnostics["environment_keys"]) <= {
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "OMP_THREAD_LIMIT",
        "PATH",
        "TESSDATA_PREFIX",
        "TMPDIR",
    }
    assert diagnostics["non_standard_descriptor_count"] <= 1


def test_worker_wire_protocol_rejects_pickle_without_executing_it(tmp_path: Path) -> None:
    marker = tmp_path / "pickle-executed"
    malicious_pickle = f"cos\nsystem\n(S'touch {marker}'\ntR.".encode()

    with pytest.raises(DocumentError, match="invalid result"):
        worker_module._decode_worker_response(
            malicious_pickle,
            max_pages=24,
            limits=IngestionLimits(),
        )

    assert not marker.exists()


def test_worker_wire_protocol_has_a_hard_receive_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_module, "DEFAULT_MAX_WORKER_MESSAGE_BYTES", 8)

    with pytest.raises(DocumentError, match="too much data"):
        worker_module._decode_worker_response(
            b"123456789",
            max_pages=24,
            limits=IngestionLimits(),
        )


@pytest.mark.asyncio
async def test_service_uses_isolated_ingestion_boundary(
    tmp_path: Path,
    repository: Repository,
    normalized_bill: NormalizedBill,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "temporary-statement.pdf"
    source.write_bytes(SYNTHETIC_PDF.read_bytes())
    readiness = ModelReadiness(
        endpoint="http://127.0.0.1:11434",
        provider=ModelProvider.OLLAMA,
        model="synthetic-local-model",
        ready=True,
        structured_output=True,
        vision=True,
        capabilities=("completion", "vision"),
        digest="provider-model-digest",
        license_excerpt="Synthetic test metadata",
        reason="Ready",
    )
    run = repository.create_run(
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_name="synthetic-statement.pdf",
        source_size=source.stat().st_size,
        media_type="application/pdf",
        source_path=str(source),
        model_endpoint="http://127.0.0.1:11434",
        model_provider="ollama",
        model_name="synthetic-local-model",
        model_digest=readiness_attestation_digest(readiness),
    )
    calls: list[tuple[Path, int]] = []

    def fake_isolated(path: Path, *, max_pages: int) -> DocumentContent:
        calls.append((path, max_pages))
        return DocumentContent(
            media_type="application/pdf",
            pages=(DocumentPage(number=1, text="synthetic statement"),),
        )

    async def fake_model_extraction(
        _gateway: object,
        document: DocumentContent,
    ) -> NormalizedBill:
        assert document.pages[0].text == "synthetic statement"
        return normalized_bill

    async def fake_probe_model(**_kwargs: object) -> ModelReadiness:
        return readiness

    monkeypatch.setattr(service_module, "extract_document_isolated", fake_isolated)
    monkeypatch.setattr(service_module, "probe_model", fake_probe_model)
    monkeypatch.setattr(
        service_module.ModelGateway,
        "extract_bill",
        fake_model_extraction,
    )
    service = WaySplitService(
        settings=Settings(data_dir=tmp_path / "data", retain_source=True, max_pages=7),
        repository=repository,
    )

    await service.process_run(run.id)

    assert calls == [(source, 7)]
    assert repository.get_run(run.id).status == "needs_review"


@pytest.mark.asyncio
async def test_service_never_builds_credentialed_gateway_for_unconfigured_endpoint(
    tmp_path: Path,
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "temporary-statement.pdf"
    source.write_bytes(SYNTHETIC_PDF.read_bytes())
    run = repository.create_run(
        source_sha256=hashlib.sha256(b"unconfigured-endpoint-run").hexdigest(),
        source_name="synthetic-statement.pdf",
        source_size=source.stat().st_size,
        media_type="application/pdf",
        source_path=str(source),
        model_endpoint="http://127.0.0.1:9090",
        model_provider="openai_compatible",
        model_name="unconfigured-model",
        model_digest="untrusted-digest",
    )

    def fake_isolated(path: Path, *, max_pages: int) -> DocumentContent:
        return DocumentContent(
            media_type="application/pdf",
            pages=(DocumentPage(number=1, text="synthetic statement"),),
        )

    class ForbiddenGateway:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("credentials must not reach an unconfigured gateway")

    monkeypatch.setattr(service_module, "extract_document_isolated", fake_isolated)
    monkeypatch.setattr(service_module, "ModelGateway", ForbiddenGateway)
    service = WaySplitService(
        settings=Settings(
            data_dir=tmp_path / "data",
            retain_source=True,
            model_endpoints=("http://127.0.0.1:11434",),
            model_api_key=SecretStr("must-not-be-sent"),
        ),
        repository=repository,
    )

    await service.process_run(run.id)

    failed = repository.get_run(run.id)
    assert failed.status == "failed"
    assert failed.error_code == "modelendpoint"
    assert "allowlist" in (failed.error_message or "")
