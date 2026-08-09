from __future__ import annotations

import base64
import io
import logging
import math
import warnings
from collections.abc import Buffer
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, UnidentifiedImageError
from pypdf import PageObject, PdfReader
from pypdf.errors import PdfReadError

from waysplit.errors import DocumentError

LOGGER = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 50_000_000
_MIN_USEFUL_PAGE_TEXT = 80
_RENDER_DPI = 160
_MAX_RENDER_PIXELS = 50_000_000

# These limits apply to the complete document, not independently to each page. They
# deliberately sit below the model gateway's request limits so an accepted document
# cannot grow without bound while pages are accumulated in memory.
DEFAULT_MAX_TOTAL_RENDER_PIXELS = 75_000_000
DEFAULT_MAX_IMAGE_BYTES = 24 * 1024 * 1024
DEFAULT_MAX_IMAGE_CHARACTERS = 36 * 1024 * 1024
DEFAULT_MAX_TEXT_CHARACTERS = 120_000


@dataclass(frozen=True, slots=True)
class IngestionLimits:
    max_total_render_pixels: int = DEFAULT_MAX_TOTAL_RENDER_PIXELS
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    max_image_characters: int = DEFAULT_MAX_IMAGE_CHARACTERS
    max_text_characters: int = DEFAULT_MAX_TEXT_CHARACTERS

    def __post_init__(self) -> None:
        if (
            min(
                self.max_total_render_pixels,
                self.max_image_bytes,
                self.max_image_characters,
                self.max_text_characters,
            )
            < 1
        ):
            raise ValueError("ingestion limits must be positive")


@dataclass(slots=True)
class _IngestionBudget:
    limits: IngestionLimits
    rendered_pixels: int = 0
    image_bytes: int = 0
    image_characters: int = 0
    text_characters: int = 0
    text_pages: int = 0

    def reserve_render_pixels(self, pixels: int) -> None:
        self.rendered_pixels = _checked_total(
            current=self.rendered_pixels,
            additional=pixels,
            maximum=self.limits.max_total_render_pixels,
            message="The statement exceeds the cumulative raster safety limit.",
        )

    def reserve_image(self, data: bytes, media_type: str) -> None:
        next_image_bytes = _checked_total(
            current=self.image_bytes,
            additional=len(data),
            maximum=self.limits.max_image_bytes,
            message="The rendered statement images exceed the encoded-image byte limit.",
        )
        encoded_characters = _data_uri_length(len(data), media_type)
        next_image_characters = _checked_total(
            current=self.image_characters,
            additional=encoded_characters,
            maximum=self.limits.max_image_characters,
            message="The rendered statement images exceed the encoded-image character limit.",
        )
        self.image_bytes = next_image_bytes
        self.image_characters = next_image_characters

    def next_image_write_limit(self, media_type: str) -> tuple[int, str]:
        remaining_bytes = self.limits.max_image_bytes - self.image_bytes
        remaining_characters = self.limits.max_image_characters - self.image_characters
        prefix_characters = len(f"data:{media_type};base64,")
        base64_groups = max(0, remaining_characters - prefix_characters) // 4
        character_limited_bytes = base64_groups * 3
        if remaining_bytes <= character_limited_bytes:
            return (
                remaining_bytes,
                "The rendered statement images exceed the encoded-image byte limit.",
            )
        return (
            character_limited_bytes,
            "The rendered statement images exceed the encoded-image character limit.",
        )

    def reserve_text(self, text: str, *, page_number: int) -> None:
        if not text:
            return
        page_prefix = f"--- PAGE {page_number} ---\n"
        separator_characters = 2 if self.text_pages else 0
        self.text_characters = _checked_total(
            current=self.text_characters,
            additional=len(page_prefix) + len(text) + separator_characters,
            maximum=self.limits.max_text_characters,
            message="The extracted statement text exceeds the cumulative safety limit.",
        )
        self.text_pages += 1

    def next_text_payload_limit(self, *, page_number: int) -> int:
        page_prefix = f"--- PAGE {page_number} ---\n"
        separator_characters = 2 if self.text_pages else 0
        overhead = len(page_prefix) + separator_characters
        remaining = self.limits.max_text_characters - self.text_characters
        return max(0, remaining - overhead)


def _checked_total(*, current: int, additional: int, maximum: int, message: str) -> int:
    if additional < 0 or current > maximum - additional:
        raise DocumentError(message)
    return current + additional


class _BoundedBytesIO(io.BytesIO):
    def __init__(self, maximum_bytes: int, error_message: str) -> None:
        super().__init__()
        self._maximum_bytes = maximum_bytes
        self._error_message = error_message
        self._size = 0

    def write(self, buffer: Buffer, /) -> int:
        buffer_size = memoryview(buffer).nbytes
        resulting_size = max(self._size, self.tell() + buffer_size)
        if resulting_size > self._maximum_bytes:
            raise DocumentError(self._error_message)
        written = super().write(buffer)
        self._size = max(self._size, self.tell())
        return written


@dataclass(frozen=True, slots=True)
class DocumentPage:
    number: int
    text: str
    image_data_uri: str | None = None
    used_ocr: bool = False


@dataclass(frozen=True, slots=True)
class DocumentContent:
    media_type: str
    pages: tuple[DocumentPage, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        return "\n\n".join(
            f"--- PAGE {page.number} ---\n{page.text}" for page in self.pages if page.text
        )

    @property
    def image_data_uris(self) -> tuple[str, ...]:
        return tuple(page.image_data_uri for page in self.pages if page.image_data_uri)


def detect_media_type(header: bytes) -> str:
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    raise DocumentError("Use a PDF, PNG, JPEG, TIFF, or WebP statement.")


def inspect_media_type(path: Path) -> str:
    with path.open("rb") as source:
        return detect_media_type(source.read(16))


def extract_document(
    path: Path,
    *,
    max_pages: int,
    limits: IngestionLimits | None = None,
) -> DocumentContent:
    """Extract a statement in-process.

    Production request handling uses :func:`extract_document_isolated` from the
    worker module. This direct entry point remains useful for tightly scoped tests
    and offline tooling.
    """

    budget = _IngestionBudget(limits or IngestionLimits())
    media_type = inspect_media_type(path)
    if media_type == "application/pdf":
        return _extract_pdf(path, max_pages=max_pages, budget=budget)
    return _extract_image(path, media_type=media_type, budget=budget)


def _extract_pdf(
    path: Path,
    *,
    max_pages: int,
    budget: _IngestionBudget,
) -> DocumentContent:
    try:
        reader = PdfReader(path, strict=False)
    except (PdfReadError, OSError, ValueError) as exc:
        raise DocumentError("The PDF could not be opened safely.") from exc

    if reader.is_encrypted:
        raise DocumentError("Password-protected PDFs are not supported yet.")
    page_count = len(reader.pages)
    if page_count < 1:
        raise DocumentError("The PDF has no pages.")
    if page_count > max_pages:
        raise DocumentError(
            f"The statement has {page_count} pages; the configured limit is {max_pages}."
        )

    pages: list[DocumentPage] = []
    extraction_warnings: list[str] = []
    renderer: pdfium.PdfDocument | None = None
    try:
        for index, page in enumerate(reader.pages):
            try:
                native_text = _extract_native_page_text(
                    page,
                    budget=budget,
                    page_number=index + 1,
                )
            except (PdfReadError, KeyError, TypeError, ValueError):
                native_text = ""
            if len(native_text) >= _MIN_USEFUL_PAGE_TEXT:
                budget.reserve_text(native_text, page_number=index + 1)
                pages.append(DocumentPage(number=index + 1, text=native_text))
                continue

            rendered_pixels = _validated_pdf_render_pixels(
                page.mediabox.width,
                page.mediabox.height,
                index=index,
            )
            # Reserve before rasterization, which is the expensive allocation.
            budget.reserve_render_pixels(rendered_pixels)
            if renderer is None:
                try:
                    renderer = pdfium.PdfDocument(path)
                except (pdfium.PdfiumError, OSError, ValueError) as exc:
                    raise DocumentError("The PDF page image could not be rendered safely.") from exc
            png = _render_pdf_page(
                renderer,
                index,
                budget=budget,
                reserved_pixels=rendered_pixels,
            )
            budget.reserve_image(png, "image/png")
            ocr_text = _run_ocr(
                png,
                page_number=index + 1,
                warnings_out=extraction_warnings,
            )
            combined_text = ocr_text or native_text
            budget.reserve_text(combined_text, page_number=index + 1)
            pages.append(
                DocumentPage(
                    number=index + 1,
                    text=combined_text,
                    image_data_uri=_data_uri(png, "image/png"),
                    used_ocr=bool(ocr_text),
                )
            )
    finally:
        if renderer is not None:
            renderer.close()

    if not any(page.text or page.image_data_uri for page in pages):
        raise DocumentError("No readable text or images were found in the PDF.")
    return DocumentContent(
        media_type="application/pdf",
        pages=tuple(pages),
        warnings=tuple(extraction_warnings),
    )


def _extract_native_page_text(
    page: PageObject,
    *,
    budget: _IngestionBudget,
    page_number: int,
) -> str:
    maximum_payload_characters = budget.next_text_payload_limit(page_number=page_number)
    visited_characters = 0

    def guard_fragment(
        text: object,
        _current_matrix: object,
        _text_matrix: object,
        _font: object,
        _font_size: object,
    ) -> None:
        nonlocal visited_characters
        if not isinstance(text, str):
            return
        visited_characters = _checked_total(
            current=visited_characters,
            additional=len(text),
            maximum=maximum_payload_characters,
            message="The extracted statement text exceeds the cumulative safety limit.",
        )

    return (page.extract_text(visitor_text=guard_fragment) or "").strip()


def _render_pdf_page(
    document: pdfium.PdfDocument,
    index: int,
    *,
    budget: _IngestionBudget,
    reserved_pixels: int,
) -> bytes:
    page = document[index]
    try:
        page_width, page_height = page.get_size()
        renderer_pixels = _validated_pdf_render_pixels(page_width, page_height, index=index)
        if renderer_pixels > reserved_pixels:
            budget.reserve_render_pixels(renderer_pixels - reserved_pixels)
        total_reserved_pixels = max(reserved_pixels, renderer_pixels)
        bitmap = page.render(scale=_RENDER_DPI / 72, rev_byteorder=True)
        try:
            image = bitmap.to_pil()
            actual_pixels = image.width * image.height
            if actual_pixels > _MAX_RENDER_PIXELS:
                raise DocumentError(f"Page {index + 1} exceeds the raster safety limit.")
            if actual_pixels > total_reserved_pixels:
                budget.reserve_render_pixels(actual_pixels - total_reserved_pixels)
            maximum_bytes, error_message = budget.next_image_write_limit("image/png")
            output = _BoundedBytesIO(maximum_bytes, error_message)
            image.save(output, format="PNG", optimize=True)
            return output.getvalue()
        finally:
            bitmap.close()
    except (pdfium.PdfiumError, OSError, ValueError) as exc:
        raise DocumentError(f"Page {index + 1} could not be rendered safely.") from exc
    finally:
        page.close()


def _validated_pdf_render_pixels(
    width_value: object,
    height_value: object,
    *,
    index: int,
) -> int:
    try:
        width_points = float(width_value)  # type: ignore[arg-type]
        height_points = float(height_value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise DocumentError(f"Page {index + 1} has invalid dimensions.") from exc
    if (
        not math.isfinite(width_points)
        or not math.isfinite(height_points)
        or width_points <= 0
        or height_points <= 0
    ):
        raise DocumentError(f"Page {index + 1} has invalid dimensions.")
    width_pixels = math.ceil(width_points * _RENDER_DPI / 72)
    height_pixels = math.ceil(height_points * _RENDER_DPI / 72)
    pixels = width_pixels * height_pixels
    if pixels > _MAX_RENDER_PIXELS:
        raise DocumentError(f"Page {index + 1} exceeds the raster safety limit.")
    return pixels


def _extract_image(
    path: Path,
    *,
    media_type: str,
    budget: _IngestionBudget,
) -> DocumentContent:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as probe:
                budget.reserve_render_pixels(probe.width * probe.height)
                probe.verify()
            with Image.open(path) as source:
                rgb = source.convert("RGB")
                rgb.thumbnail((3000, 3000), Image.Resampling.LANCZOS)
                maximum_bytes, error_message = budget.next_image_write_limit("image/jpeg")
                output = _BoundedBytesIO(maximum_bytes, error_message)
                rgb.save(output, format="JPEG", quality=88, optimize=True)
                normalized = output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DocumentError("The image could not be decoded safely.") from exc
    except Image.DecompressionBombWarning as exc:
        raise DocumentError("The image dimensions exceed the safety limit.") from exc

    budget.reserve_image(normalized, "image/jpeg")
    extraction_warnings: list[str] = []
    text = _run_ocr(normalized, page_number=1, warnings_out=extraction_warnings)
    budget.reserve_text(text, page_number=1)
    return DocumentContent(
        media_type=media_type,
        pages=(
            DocumentPage(
                number=1,
                text=text,
                image_data_uri=_data_uri(normalized, "image/jpeg"),
                used_ocr=bool(text),
            ),
        ),
        warnings=tuple(extraction_warnings),
    )


def _run_ocr(data: bytes, *, page_number: int, warnings_out: list[str]) -> str:
    try:
        with Image.open(io.BytesIO(data)) as image:
            result = pytesseract.image_to_string(image, config="--psm 6", timeout=60)
            return str(result).strip()
    except (pytesseract.TesseractNotFoundError, RuntimeError) as exc:
        LOGGER.info("OCR unavailable for page %s: %s", page_number, type(exc).__name__)
        warnings_out.append(
            f"Page {page_number}: local OCR was unavailable; a vision-capable model is required."
        )
        return ""
    except pytesseract.TesseractError as exc:
        LOGGER.warning("OCR failed for page %s: %s", page_number, type(exc).__name__)
        warnings_out.append(
            f"Page {page_number}: OCR could not read the page; review extraction carefully."
        )
        return ""


def _data_uri(data: bytes, media_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _data_uri_length(data_length: int, media_type: str) -> int:
    base64_length = 4 * ((data_length + 2) // 3)
    return len(f"data:{media_type};base64,") + base64_length
