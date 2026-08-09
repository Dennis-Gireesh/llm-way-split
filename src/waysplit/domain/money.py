"""Exact decimal money primitives used by the deterministic domain layer."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_ABSOLUTE_MONEY = Decimal("999999999.99")
MAX_MONEY_TEXT_LENGTH = 13

_MONEY_PATTERN = re.compile(r"^[+-]?\d+(?:\.\d{1,2})?$")


def parse_money(value: Any) -> Decimal:
    """Return a cent-quantized Decimal, refusing all floating-point input.

    JSON extraction contracts should represent money as strings. Refusing floats
    prevents a binary floating-point approximation from ever entering the ledger
    or allocation engine.
    """

    if isinstance(value, (bool, float)):
        raise ValueError(
            "money must be a decimal string, integer, or Decimal; floats are forbidden"
        )

    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, int):
        decimal_value = Decimal(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if len(stripped) > MAX_MONEY_TEXT_LENGTH:
            raise ValueError("money exceeds the supported amount range")
        if not _MONEY_PATTERN.fullmatch(stripped):
            raise ValueError("money must be a plain decimal with at most two fractional digits")
        try:
            decimal_value = Decimal(stripped)
        except InvalidOperation as exc:  # pragma: no cover - guarded by the regex
            raise ValueError("invalid monetary amount") from exc
    else:
        raise ValueError("money must be a decimal string, integer, or Decimal")

    if not decimal_value.is_finite():
        raise ValueError("money must be finite")
    if abs(decimal_value) > MAX_ABSOLUTE_MONEY:
        raise ValueError(f"money must be between -{MAX_ABSOLUTE_MONEY} and {MAX_ABSOLUTE_MONEY}")

    normalized = decimal_value.normalize()
    exponent = normalized.as_tuple().exponent
    if not isinstance(exponent, int):  # Decimal typing also models NaN/Infinity exponents.
        raise ValueError("money must be finite")
    if normalized != 0 and exponent < -2:
        raise ValueError("money cannot contain fractions smaller than one cent")

    digits = len(decimal_value.as_tuple().digits)
    decimal_exponent = decimal_value.as_tuple().exponent
    if not isinstance(decimal_exponent, int):  # pragma: no cover - finite check above
        raise ValueError("money must be finite")
    with localcontext() as context:
        context.prec = max(28, digits + abs(decimal_exponent) + 4)
        quantized = decimal_value.quantize(CENT)
    return ZERO if quantized == 0 else quantized


def format_money(value: Decimal) -> str:
    """Render money without exponents and with exactly two fractional digits."""

    return format(value, ".2f")


Money = Annotated[
    Decimal,
    BeforeValidator(parse_money),
    PlainSerializer(format_money, return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "maxLength": MAX_MONEY_TEXT_LENGTH,
            "pattern": r"^[+-]?\d+(?:\.\d{1,2})?$",
            "examples": ["123.45", "-10.00"],
            "description": (
                "Exact monetary amount expressed as a decimal string; JSON numbers are rejected "
                f"and absolute values cannot exceed {MAX_ABSOLUTE_MONEY}."
            ),
        },
        mode="validation",
    ),
]
