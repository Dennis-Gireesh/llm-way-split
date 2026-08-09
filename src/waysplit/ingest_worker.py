from __future__ import annotations

import ctypes
import json
import logging
import multiprocessing
import os
import shutil
import signal
import sys
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path

from waysplit.errors import DocumentError
from waysplit.ingest import DocumentContent, DocumentPage, IngestionLimits, extract_document

DEFAULT_INGESTION_TIMEOUT_SECONDS = 180.0
DEFAULT_INGESTION_MEMORY_BYTES = 1024 * 1024 * 1024
DEFAULT_INGESTION_CPU_SECONDS = 120
DEFAULT_MAX_WORKER_MESSAGE_BYTES = 40 * 1024 * 1024
_WORKER_EXIT_GRACE_SECONDS = 2.0
_MAX_WORKER_ERROR_CHARACTERS = 500
_MAX_WORKER_WARNING_CHARACTERS = 500
_SUPPORTED_MEDIA_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}
_STANDARD_EXECUTABLE_DIRECTORIES = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
)
_PRESERVED_LOCALE_VARIABLES = ("LANG", "LC_ALL", "LC_CTYPE")


@dataclass(frozen=True, slots=True)
class WorkerResourceLimits:
    memory_bytes: int = DEFAULT_INGESTION_MEMORY_BYTES
    cpu_seconds: int = DEFAULT_INGESTION_CPU_SECONDS

    def __post_init__(self) -> None:
        if self.memory_bytes < 1 or self.cpu_seconds < 1:
            raise ValueError("worker resource limits must be positive")


def extract_document_isolated(
    path: Path,
    *,
    max_pages: int,
    limits: IngestionLimits | None = None,
    timeout_seconds: float = DEFAULT_INGESTION_TIMEOUT_SECONDS,
    resource_limits: WorkerResourceLimits | None = None,
) -> DocumentContent:
    """Extract an untrusted statement in a bounded, disposable POSIX process."""

    if os.name != "posix":
        raise DocumentError("Isolated statement processing requires macOS or Linux.")
    if timeout_seconds <= 0:
        raise ValueError("ingestion timeout must be positive")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    try:
        source_path = path.resolve(strict=True)
    except OSError as exc:
        raise DocumentError("The temporary statement file is unavailable.") from exc
    if not source_path.is_file():
        raise DocumentError("The temporary statement file is unavailable.")

    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker_entry,
        args=(
            child_connection,
            str(source_path),
            max_pages,
            limits or IngestionLimits(),
            resource_limits or WorkerResourceLimits(),
        ),
        name="waysplit-statement-ingestion",
    )
    try:
        process.start()
    except (OSError, RuntimeError) as exc:
        parent_connection.close()
        child_connection.close()
        raise DocumentError("The isolated statement processor could not start safely.") from exc
    child_connection.close()

    try:
        if not parent_connection.poll(timeout_seconds):
            _stop_worker(process)
            process.close()
            raise DocumentError("Statement processing exceeded the wall-clock safety limit.")
        try:
            raw_response = parent_connection.recv_bytes(maxlength=DEFAULT_MAX_WORKER_MESSAGE_BYTES)
        except (EOFError, OSError) as exc:
            process.join(_WORKER_EXIT_GRACE_SECONDS)
            if process.is_alive():
                _stop_worker(process)
            process.close()
            raise DocumentError(
                "The isolated statement processor stopped before completing. "
                "It may have exceeded a resource limit."
            ) from exc
    finally:
        parent_connection.close()

    process.join(_WORKER_EXIT_GRACE_SECONDS)
    if process.is_alive():
        _stop_worker(process)
        process.close()
        raise DocumentError("The isolated statement processor did not exit safely.")
    process.close()
    return _decode_worker_response(
        raw_response,
        max_pages=max_pages,
        limits=limits or IngestionLimits(),
    )


def _worker_entry(
    connection: Connection,
    source_path: str,
    max_pages: int,
    limits: IngestionLimits,
    resource_limits: WorkerResourceLimits,
) -> None:
    try:
        os.setsid()
        _scrub_worker_environment()
        _close_unrelated_file_descriptors(keep={connection.fileno()})
        _apply_no_new_privileges()
        _apply_resource_limits(resource_limits)
        _silence_worker_output()
        document = extract_document(
            Path(source_path),
            max_pages=max_pages,
            limits=limits,
        )
        _send_worker_response(
            connection,
            {
                "status": "ok",
                "document": {
                    "media_type": document.media_type,
                    "pages": [
                        {
                            "number": page.number,
                            "text": page.text,
                            "image_data_uri": page.image_data_uri,
                            "used_ocr": page.used_ocr,
                        }
                        for page in document.pages
                    ],
                    "warnings": list(document.warnings),
                },
            },
        )
    except DocumentError as exc:
        _send_worker_response(
            connection,
            {"status": "document_error", "message": str(exc)[:_MAX_WORKER_ERROR_CHARACTERS]},
        )
    except MemoryError:
        _send_worker_response(connection, {"status": "resource_error"})
    except Exception:
        # Never serialize tracebacks, parser details, document text, or source paths
        # across this trust boundary.
        _send_worker_response(connection, {"status": "internal_error"})
    finally:
        connection.close()


def _send_worker_response(connection: Connection, payload: dict[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > DEFAULT_MAX_WORKER_MESSAGE_BYTES:
        encoded = b'{"status":"resource_error"}'
    connection.send_bytes(encoded)


def _decode_worker_response(
    raw_response: bytes,
    *,
    max_pages: int,
    limits: IngestionLimits,
) -> DocumentContent:
    if len(raw_response) > DEFAULT_MAX_WORKER_MESSAGE_BYTES:
        raise DocumentError("The isolated statement processor returned too much data.")
    try:
        response = json.loads(raw_response.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise DocumentError("The isolated statement processor returned an invalid result.") from exc
    if not isinstance(response, dict):
        raise DocumentError("The isolated statement processor returned an invalid result.")
    status_value = response.get("status")
    if status_value == "document_error" and set(response) == {"status", "message"}:
        message = response.get("message")
        if isinstance(message, str) and 0 < len(message) <= _MAX_WORKER_ERROR_CHARACTERS:
            raise DocumentError(message)
    if status_value == "resource_error" and set(response) == {"status"}:
        raise DocumentError("The isolated statement processor exceeded a safety limit.")
    if status_value == "internal_error" and set(response) == {"status"}:
        raise DocumentError("The isolated statement processor failed safely.")
    if status_value != "ok" or set(response) != {"status", "document"}:
        raise DocumentError("The isolated statement processor returned an invalid result.")
    return _decode_document(
        response.get("document"),
        max_pages=max_pages,
        limits=limits,
    )


def _decode_document(
    value: object,
    *,
    max_pages: int,
    limits: IngestionLimits,
) -> DocumentContent:
    if not isinstance(value, dict) or set(value) != {"media_type", "pages", "warnings"}:
        raise DocumentError("The isolated statement processor returned an invalid document.")
    media_type = value.get("media_type")
    pages_value = value.get("pages")
    warnings_value = value.get("warnings")
    if (
        not isinstance(media_type, str)
        or media_type not in _SUPPORTED_MEDIA_TYPES
        or not isinstance(pages_value, list)
        or not 1 <= len(pages_value) <= max_pages
        or not isinstance(warnings_value, list)
        or len(warnings_value) > max_pages
    ):
        raise DocumentError("The isolated statement processor returned an invalid document.")

    warnings: list[str] = []
    for warning in warnings_value:
        if not isinstance(warning, str) or len(warning) > _MAX_WORKER_WARNING_CHARACTERS:
            raise DocumentError("The isolated statement processor returned invalid warnings.")
        warnings.append(warning)

    pages: list[DocumentPage] = []
    for expected_number, page_value in enumerate(pages_value, start=1):
        if not isinstance(page_value, dict) or set(page_value) != {
            "number",
            "text",
            "image_data_uri",
            "used_ocr",
        }:
            raise DocumentError("The isolated statement processor returned an invalid page.")
        number = page_value.get("number")
        text = page_value.get("text")
        image_data_uri = page_value.get("image_data_uri")
        used_ocr = page_value.get("used_ocr")
        if (
            type(number) is not int
            or number != expected_number
            or not isinstance(text, str)
            or (image_data_uri is not None and not isinstance(image_data_uri, str))
            or not isinstance(used_ocr, bool)
        ):
            raise DocumentError("The isolated statement processor returned an invalid page.")
        if image_data_uri is not None and not image_data_uri.startswith(
            ("data:image/png;base64,", "data:image/jpeg;base64,")
        ):
            raise DocumentError("The isolated statement processor returned an invalid page image.")
        pages.append(
            DocumentPage(
                number=number,
                text=text,
                image_data_uri=image_data_uri,
                used_ocr=used_ocr,
            )
        )

    document = DocumentContent(
        media_type=media_type,
        pages=tuple(pages),
        warnings=tuple(warnings),
    )
    if (
        len(document.text) > limits.max_text_characters
        or sum(len(image) for image in document.image_data_uris) > limits.max_image_characters
    ):
        raise DocumentError("The isolated statement processor returned an oversized document.")
    if not any(page.text or page.image_data_uri for page in document.pages):
        raise DocumentError("The isolated statement processor returned an empty document.")
    return document


def _apply_resource_limits(limits: WorkerResourceLimits) -> None:
    import resource

    _set_required_limit(resource.RLIMIT_CPU, limits.cpu_seconds, limits.cpu_seconds + 1)
    if sys.platform.startswith("linux"):
        _set_required_limit(resource.RLIMIT_AS, limits.memory_bytes, limits.memory_bytes)
    else:
        # RLIMIT_AS includes enormous shared regions on macOS. DATA is enforceable for
        # some processes; RSS is also requested but may be advisory by kernel. Darwin
        # can reject both after its shared runtime regions are mapped, so neither is a
        # safe prerequisite for starting the otherwise bounded disposable worker.
        _set_optional_limit(resource.RLIMIT_DATA, limits.memory_bytes, limits.memory_bytes)
        if hasattr(resource, "RLIMIT_RSS"):
            _set_optional_limit(resource.RLIMIT_RSS, limits.memory_bytes, limits.memory_bytes)
    _set_optional_limit(resource.RLIMIT_CORE, 0, 0)


def _scrub_worker_environment() -> None:
    """Keep only non-secret values required by local OCR."""

    inherited = dict(os.environ)
    tesseract = shutil.which("tesseract", path=inherited.get("PATH"))
    executable_directories: list[str] = []
    if tesseract:
        executable_directories.append(str(Path(tesseract).resolve().parent))
    executable_directories.extend(
        directory for directory in _STANDARD_EXECUTABLE_DIRECTORIES if Path(directory).is_dir()
    )
    safe_environment = {
        "PATH": os.pathsep.join(dict.fromkeys(executable_directories)),
        "TMPDIR": "/tmp",  # noqa: S108 - container/native bounded temporary root
        "OMP_THREAD_LIMIT": "1",
    }
    for name in _PRESERVED_LOCALE_VARIABLES:
        value = inherited.get(name)
        if value and len(value) <= 128 and "\x00" not in value:
            safe_environment[name] = value
    if not any(name in safe_environment for name in _PRESERVED_LOCALE_VARIABLES):
        safe_environment["LANG"] = "C.UTF-8"
    tessdata_prefix = inherited.get("TESSDATA_PREFIX")
    if tessdata_prefix and len(tessdata_prefix) <= 4096 and Path(tessdata_prefix).is_absolute():
        safe_environment["TESSDATA_PREFIX"] = tessdata_prefix
    os.environ.clear()
    os.environ.update(safe_environment)


def _close_unrelated_file_descriptors(*, keep: set[int]) -> None:
    keep_fds = {0, 1, 2, *keep}
    open_fds = _open_file_descriptors()
    for descriptor in open_fds:
        if descriptor in keep_fds:
            continue
        try:
            os.close(descriptor)
        except OSError:
            continue


def _open_file_descriptors() -> tuple[int, ...]:
    for directory_name in ("/proc/self/fd", "/dev/fd"):
        directory = Path(directory_name)
        try:
            return tuple(int(entry.name) for entry in directory.iterdir() if entry.name.isdigit())
        except OSError:
            continue
    import resource

    soft_limit, _hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    maximum = 65_536 if soft_limit == resource.RLIM_INFINITY else min(soft_limit, 65_536)
    return tuple(range(3, int(maximum)))


def _apply_no_new_privileges() -> None:
    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )
    prctl.restype = ctypes.c_int
    # Linux PR_SET_DUMPABLE=4 and PR_SET_NO_NEW_PRIVS=38.
    if prctl(4, 0, 0, 0, 0) != 0 or prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError("The statement worker could not apply Linux process hardening.")


def _silence_worker_output() -> None:
    """Prevent parser/OCR diagnostics from becoming application logs."""

    logging.disable(logging.CRITICAL)
    sink = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(sink, 1)
        os.dup2(sink, 2)
    finally:
        os.close(sink)


def _set_required_limit(kind: int, soft: int, hard: int) -> None:
    import resource

    current_soft, current_hard = resource.getrlimit(kind)
    effective_hard = hard if current_hard == resource.RLIM_INFINITY else min(hard, current_hard)
    effective_soft = min(soft, effective_hard)
    if current_soft != resource.RLIM_INFINITY:
        effective_soft = min(effective_soft, current_soft)
    resource.setrlimit(kind, (effective_soft, effective_hard))


def _set_optional_limit(kind: int, soft: int, hard: int) -> None:
    try:
        _set_required_limit(kind, soft, hard)
    except (OSError, ValueError):
        return


def _stop_worker(process: BaseProcess) -> None:
    if not process.is_alive():
        process.join()
        return
    _signal_process_group(process, signal.SIGTERM)
    process.join(_WORKER_EXIT_GRACE_SECONDS)
    if process.is_alive():
        _signal_process_group(process, signal.SIGKILL)
        process.join(_WORKER_EXIT_GRACE_SECONDS)


def _signal_process_group(process: BaseProcess, requested_signal: int) -> None:
    pid = process.pid
    if pid is not None:
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, requested_signal)
                return
        except (OSError, ProcessLookupError):
            pass
    if requested_signal == signal.SIGKILL:
        process.kill()
    else:
        process.terminate()
