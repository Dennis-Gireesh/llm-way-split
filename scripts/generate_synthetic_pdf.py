#!/usr/bin/env python3
"""Generate the deterministic, project-authored Example Mobile PDF fixture."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "examples" / "synthetic" / "example-mobile-statement.pdf"

LINES = (
    "EXAMPLE MOBILE - SYNTHETIC STATEMENT",
    "Reference: example-statement-2026-08",
    "Account: example-account-alpha",
    "Issued: 2026-08-05",
    "Period: 2026-07-01 through 2026-07-31",
    "",
    "Service Alpha monthly plan                         $30.00",
    "Service Beta equipment installment                 $12.00",
    "Account taxes                                       $3.00",
    "",
    "Current charges                                    $45.00",
    "Balance forward                                    $10.00",
    "Payments and credits                              -$10.00",
    "Other adjustments                                   $0.00",
    "AMOUNT DUE                                         $45.00",
    "",
    "This document is fictional and contains no customer data.",
)


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def generate() -> bytes:
    commands = ["BT", "/F1 12 Tf", "50 742 Td"]
    for index, line in enumerate(LINES):
        if index:
            commands.append("0 -28 Td" if index in {1, 6, 10, 16} else "0 -20 Td")
        commands.append(f"({_escape_pdf_text(line)}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]

    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii"))
        document.extend(body)
        document.extend(b"\nendobj\n")

    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(document)


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(generate())
    print(OUTPUT)


if __name__ == "__main__":
    main()
