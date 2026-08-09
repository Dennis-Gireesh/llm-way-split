from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

DEFAULT_MODEL_ENDPOINTS = (
    "http://127.0.0.1:11434",
    "http://127.0.0.1:8080",
)


class Settings(BaseModel):
    """Runtime settings with environment variables taking precedence over YAML."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=9876, ge=1, le=65535)
    data_dir: Path = Path("./data")
    retain_source: bool = False
    max_upload_mib: int = Field(default=25, ge=1, le=250)
    max_pages: int = Field(default=24, ge=1, le=100)
    model_endpoints: tuple[str, ...] = DEFAULT_MODEL_ENDPOINTS
    allow_remote_model_endpoints: bool = False
    model_api_key: SecretStr | None = None
    model_timeout_seconds: int = Field(default=300, ge=10, le=1800)
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:9876",
        "http://localhost:9876",
    )
    trust_proxy_headers: bool = False
    reconciliation_tolerance: Decimal = Field(default=Decimal("0.00"), ge=0, le=0)
    minimum_extraction_confidence: Decimal = Field(default=Decimal("0.80"), ge=0, le=1)
    require_charge_evidence: bool = True
    log_level: str = "INFO"

    @field_validator("model_endpoints", "allowed_origins")
    @classmethod
    def strip_urls(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(
            dict.fromkeys(value.strip().rstrip("/") for value in values if value.strip())
        )
        if not cleaned:
            raise ValueError("at least one endpoint or origin is required")
        return cleaned

    @field_validator("allow_remote_model_endpoints")
    @classmethod
    def remote_models_are_not_supported_in_v0_1(cls, value: bool) -> bool:
        if value:
            raise ValueError("remote model endpoints are not supported in WaySplit 0.1")
        return False

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return normalized

    @property
    def database_path(self) -> Path:
        return self.data_dir / "waysplit.sqlite3"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mib * 1024 * 1024

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.upload_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)
        self.upload_dir.chmod(0o700)


def _as_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _yaml_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")

    allowed_root = {"app", "models", "allocation"}
    unknown_root = sorted(set(raw) - allowed_root)
    if unknown_root:
        raise ValueError(f"unknown config sections: {unknown_root}")

    app = raw.get("app", {}) or {}
    models = raw.get("models", {}) or {}
    allocation = raw.get("allocation", {}) or {}
    if not all(isinstance(section, dict) for section in (app, models, allocation)):
        raise ValueError("app, models, and allocation sections must be mappings")

    allowed_keys = {
        "app": {
            "host",
            "port",
            "data_dir",
            "retain_source",
            "max_upload_mib",
            "max_pages",
        },
        "models": {"endpoints", "timeout_seconds"},
        "allocation": {
            "reconciliation_tolerance",
            "minimum_extraction_confidence",
            "require_charge_evidence",
        },
    }
    for section_name, section in (
        ("app", app),
        ("models", models),
        ("allocation", allocation),
    ):
        unknown = sorted(set(section) - allowed_keys[section_name])
        if unknown:
            raise ValueError(f"unknown {section_name} config keys: {unknown}")

    mapped: dict[str, Any] = {}
    for source, target in (
        ("host", "host"),
        ("port", "port"),
        ("data_dir", "data_dir"),
        ("retain_source", "retain_source"),
        ("max_upload_mib", "max_upload_mib"),
        ("max_pages", "max_pages"),
    ):
        if source in app:
            mapped[target] = app[source]
    if "endpoints" in models:
        if not isinstance(models["endpoints"], list) or not all(
            isinstance(value, str) for value in models["endpoints"]
        ):
            raise ValueError("models.endpoints must be a list of URL strings")
        mapped["model_endpoints"] = tuple(models["endpoints"])
    if "timeout_seconds" in models:
        mapped["model_timeout_seconds"] = models["timeout_seconds"]
    if "reconciliation_tolerance" in allocation:
        mapped["reconciliation_tolerance"] = allocation["reconciliation_tolerance"]
    if "minimum_extraction_confidence" in allocation:
        mapped["minimum_extraction_confidence"] = allocation["minimum_extraction_confidence"]
    if "require_charge_evidence" in allocation:
        mapped["require_charge_evidence"] = allocation["require_charge_evidence"]
    return mapped


def load_settings(config_path: Path | None = None) -> Settings:
    """Load safe configuration from YAML, then overlay WAYSPLIT_* environment values."""

    dotenv = {key: value for key, value in dotenv_values(".env").items() if value is not None}
    environment = {**dotenv, **os.environ}
    explicit_path = config_path or (
        Path(environment["WAYSPLIT_CONFIG"]) if environment.get("WAYSPLIT_CONFIG") else None
    )
    values = _yaml_settings(explicit_path) if explicit_path else {}

    env_mapping: dict[str, tuple[str, Any]] = {
        "WAYSPLIT_HOST": ("host", str),
        "WAYSPLIT_PORT": ("port", int),
        "WAYSPLIT_DATA_DIR": ("data_dir", Path),
        "WAYSPLIT_RETAIN_SOURCE": ("retain_source", _as_bool),
        "WAYSPLIT_MAX_UPLOAD_MIB": ("max_upload_mib", int),
        "WAYSPLIT_MAX_PAGES": ("max_pages", int),
        "WAYSPLIT_MODEL_ENDPOINTS": ("model_endpoints", _csv),
        "WAYSPLIT_MODEL_API_KEY": (
            "model_api_key",
            lambda item: SecretStr(item) if item else None,
        ),
        "WAYSPLIT_MODEL_TIMEOUT_SECONDS": ("model_timeout_seconds", int),
        "WAYSPLIT_ALLOWED_ORIGINS": ("allowed_origins", _csv),
        "WAYSPLIT_TRUST_PROXY_HEADERS": ("trust_proxy_headers", _as_bool),
        "WAYSPLIT_RECONCILIATION_TOLERANCE": ("reconciliation_tolerance", Decimal),
        "WAYSPLIT_MINIMUM_EXTRACTION_CONFIDENCE": (
            "minimum_extraction_confidence",
            Decimal,
        ),
        "WAYSPLIT_REQUIRE_CHARGE_EVIDENCE": ("require_charge_evidence", _as_bool),
        "WAYSPLIT_LOG_LEVEL": ("log_level", str),
    }
    unknown_environment = sorted(
        name
        for name in environment
        if name.startswith("WAYSPLIT_")
        and name not in env_mapping
        and name not in {"WAYSPLIT_CONFIG", "WAYSPLIT_BROWSER_ACCESS_TOKEN"}
    )
    if unknown_environment:
        raise ValueError(f"unknown WaySplit environment settings: {unknown_environment}")
    for env_name, (field_name, parser) in env_mapping.items():
        if env_name in environment:
            values[field_name] = parser(environment[env_name])
    return Settings.model_validate(values)
