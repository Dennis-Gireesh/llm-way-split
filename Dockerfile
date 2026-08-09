# syntax=docker/dockerfile:1.10.0@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5

# Both images are immutable multi-platform manifests. Dependabot updates the
# human-readable tag and digest together.
ARG PYTHON_IMAGE="python:3.12.13-alpine3.23@sha256:601d3d3797e90e2534782e69c85fafb7971b43f24c7b1b079b7e48dd435e458d"
ARG UV_IMAGE="ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36"

FROM ${UV_IMAGE} AS uv-bin

FROM ${PYTHON_IMAGE} AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY --from=uv-bin /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md LICENSE ./

# Cache the locked third-party environment independently of application code.
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime

ARG VERSION="0.1.0"
ARG VCS_REF="unknown"

LABEL org.opencontainers.image.title="WaySplit" \
      org.opencontainers.image.description="Local-first, deterministic mobile bill splitting" \
      org.opencontainers.image.source="https://github.com/Dennis-Gireesh/llm-way-split" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    WAYSPLIT_HOST=0.0.0.0 \
    WAYSPLIT_PORT=9876 \
    WAYSPLIT_DATA_DIR=/data

# English OCR data is installed explicitly at exact Alpine package versions.
RUN apk add --no-cache \
        tesseract-ocr=5.5.1-r0 \
        tesseract-ocr-data-eng=5.5.1-r0 \
    && addgroup -g 10001 -S waysplit \
    && adduser -u 10001 -S -D -H -G waysplit waysplit \
    && mkdir -p /data \
    && chown 10001:10001 /data \
    && chmod 0700 /data \
    && tesseract --version >/dev/null

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

USER 10001:10001

EXPOSE 9876
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:9876/api/health', timeout=3); payload = json.load(response); raise SystemExit(0 if payload.get('status') == 'ok' else 1)"]

CMD ["waysplit", "serve"]
