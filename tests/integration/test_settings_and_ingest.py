from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from waysplit.errors import DocumentError, ModelEndpointError, PostingBlockedError
from waysplit.ingest import detect_media_type, inspect_media_type
from waysplit.model_gateway import normalize_endpoint
from waysplit.settings import Settings, load_settings
from waysplit.web import _store_upload


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in tuple(os.environ):
        if name.startswith("WAYSPLIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def test_environment_and_dotenv_override_yaml_in_that_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
app:
  host: 127.0.0.1
  port: 9100
  data_dir: yaml-data
  retain_source: true
  max_upload_mib: 20
models:
  endpoints:
    - http://127.0.0.1:11434/
allocation:
  reconciliation_tolerance: "0.00"
  minimum_extraction_confidence: "0.75"
  require_charge_evidence: false
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "WAYSPLIT_PORT=9200\nWAYSPLIT_MODEL_TIMEOUT_SECONDS=480\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WAYSPLIT_PORT", "9876")
    monkeypatch.setenv("WAYSPLIT_DATA_DIR", str(tmp_path / "environment-data"))

    settings = load_settings(config)

    assert settings.host == "127.0.0.1"
    assert settings.port == 9876
    assert settings.data_dir == tmp_path / "environment-data"
    assert settings.retain_source is True
    assert settings.model_endpoints == ("http://127.0.0.1:11434",)
    assert settings.model_timeout_seconds == 480
    assert str(settings.reconciliation_tolerance) == "0.00"
    assert str(settings.minimum_extraction_confidence) == "0.75"
    assert settings.require_charge_evidence is False


@pytest.mark.parametrize(
    "content",
    [
        "unexpected:\n  value: true\n",
        "models:\n  default_model: stale-knob\n",
        "allocation:\n  currency: USD\n",
    ],
)
def test_unknown_yaml_keys_fail_fast(content: str, tmp_path: Path) -> None:
    config = tmp_path / "invalid-config.yaml"
    config.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="unknown"):
        load_settings(config)


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
        ("http://localhost:8080/v1", "http://localhost:8080"),
        ("http://[::1]:11434/v1/", "http://[::1]:11434"),
        ("http://ollama:11434", "http://ollama:11434"),
    ],
)
def test_local_model_endpoint_normalization(endpoint: str, expected: str) -> None:
    assert normalize_endpoint(endpoint, allow_remote=False) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://models.example.invalid/v1",
        "http://192.0.2.20:11434",
        "http://user:secret@127.0.0.1:11434",
        "file:///tmp/model.sock",
        "http://127.0.0.1:11434?token=secret",
    ],
)
def test_unsafe_or_remote_model_endpoints_are_rejected_by_default(endpoint: str) -> None:
    with pytest.raises(ModelEndpointError):
        normalize_endpoint(endpoint, allow_remote=False)


def test_remote_model_endpoint_requires_explicit_opt_in() -> None:
    assert (
        normalize_endpoint("https://models.example.invalid/v1", allow_remote=True)
        == "https://models.example.invalid"
    )


def test_application_configuration_rejects_remote_model_mode() -> None:
    with pytest.raises(ValueError, match="not supported"):
        Settings(allow_remote_model_endpoints=True)


def test_legacy_browser_access_token_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYSPLIT_BROWSER_ACCESS_TOKEN", "old-token-no-longer-used")
    settings = load_settings()
    assert not hasattr(settings, "browser_access_token")


@pytest.mark.parametrize(
    ("header", "media_type"),
    [
        (b"%PDF-1.7\n", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"RIFF1234WEBP", "image/webp"),
    ],
)
def test_media_type_comes_from_magic_bytes(header: bytes, media_type: str) -> None:
    assert detect_media_type(header) == media_type


def test_file_extension_cannot_override_invalid_magic(tmp_path: Path) -> None:
    masquerading = tmp_path / "statement.pdf"
    masquerading.write_bytes(b"not a statement")

    with pytest.raises(DocumentError, match="PDF, PNG, JPEG, TIFF, or WebP"):
        inspect_media_type(masquerading)


@pytest.mark.asyncio
async def test_upload_limit_is_enforced_and_partial_file_is_erased(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    upload = UploadFile(filename="example-statement.pdf", file=io.BytesIO(b"%PDF-1.7\n"))

    with pytest.raises(PostingBlockedError, match="exceeds"):
        await _store_upload(upload, upload_dir=upload_dir, max_bytes=4)

    assert list(upload_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_upload_uses_magic_type_and_sanitizes_path_components(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    upload = UploadFile(
        filename="../../Example Mobile statement.pdf",
        file=io.BytesIO(b"%PDF-1.7\nsynthetic"),
    )

    path, digest, size, media_type, source_name = await _store_upload(
        upload,
        upload_dir=upload_dir,
        max_bytes=1024,
    )

    assert path.parent == upload_dir
    assert len(digest) == 64
    assert size == len(b"%PDF-1.7\nsynthetic")
    assert media_type == "application/pdf"
    assert source_name == "Example Mobile statement.pdf"
