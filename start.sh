#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Start WaySplit and open it in your browser."
  exit 0
fi

if [[ $# -gt 0 ]]; then
  echo "Usage: ./start.sh" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Desktop is required. Install it, start it, and run this again." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is not running. Start it, then run this again." >&2
  exit 1
fi

# Keep the release reproducible while allowing an existing .env to hold local
# model settings. The browser unlock/password setting is intentionally unused.
if [[ ! -f .env ]]; then
  printf '%s\n' 'WAYSPLIT_IMAGE=ghcr.io/dennis-gireesh/llm-way-split:v0.1.4' > .env
  chmod 600 .env
fi

echo "Starting WaySplit..."
configured_image=$(sed -n 's/^WAYSPLIT_IMAGE=//p' .env | tail -n 1)
if [[ "$configured_image" == ghcr.io/* ]]; then
  docker compose pull waysplit
fi
docker compose up --detach waysplit

for _ in {1..60}; do
  if curl --silent --fail http://127.0.0.1:9876/api/health >/dev/null 2>&1; then
    echo "WaySplit is ready: http://127.0.0.1:9876"
    if command -v open >/dev/null 2>&1; then
      open http://127.0.0.1:9876 >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open http://127.0.0.1:9876 >/dev/null 2>&1 || true
    fi
    exit 0
  fi
  sleep 1
done

echo "WaySplit did not become ready. Check Docker Desktop logs for the waysplit service." >&2
exit 1
