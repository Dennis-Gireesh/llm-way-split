"""Canonical JSON helpers shared by fingerprints and the audit ledger."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("canonical JSON does not permit non-finite decimals")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def canonical_value(value: Any) -> Any:
    """Convert supported rich values to deterministic JSON-compatible values."""

    if isinstance(value, BaseModel):
        return canonical_value(value.model_dump(mode="python", by_alias=True))
    if is_dataclass(value) and not isinstance(value, type):
        return canonical_value(asdict(value))
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical datetimes must include a timezone")
        utc_value = value.astimezone(UTC)
        return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            converted[key] = canonical_value(item)
        return converted
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted_items = [canonical_value(item) for item in value]
        return sorted(converted_items, key=canonical_json)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON does not permit non-finite floats")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
