#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
app_image=${WAYSPLIT_SBOM_IMAGE:-waysplit:sbom}
trivy_image='aquasec/trivy:0.73.0@sha256:7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c'
sbom_cdx="$repo_root/sbom.cyclonedx.json"
sbom_spdx="$repo_root/sbom.spdx.json"

docker build --tag "$app_image" "$repo_root"

if command -v trivy >/dev/null 2>&1; then
  trivy image --format cyclonedx --output "$sbom_cdx" "$app_image"
  trivy image --format spdx-json --output "$sbom_spdx" "$app_image"
else
  # Docker Desktop only bind-mounts configured host paths. Keep the temporary
  # archive beneath the ignored project data directory for Mac/Linux parity.
  mkdir -p "$repo_root/data"
  sbom_tmp=$(mktemp -d "$repo_root/data/.waysplit-sbom.XXXXXX")
  trap 'rm -rf -- "$sbom_tmp"' EXIT
  mkdir -p "$sbom_tmp/cache"
  docker save --output "$sbom_tmp/image.tar" "$app_image"
  docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev \
    --volume "$sbom_tmp:/work" \
    "$trivy_image" image --input /work/image.tar --cache-dir /work/cache \
    --format cyclonedx --output /work/sbom.cyclonedx.json
  docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev \
    --volume "$sbom_tmp:/work" \
    "$trivy_image" image --input /work/image.tar --cache-dir /work/cache \
    --format spdx-json --output /work/sbom.spdx.json
  install -m 0644 "$sbom_tmp/sbom.cyclonedx.json" "$sbom_cdx"
  install -m 0644 "$sbom_tmp/sbom.spdx.json" "$sbom_spdx"
fi

printf 'Generated %s and %s\n' "$sbom_cdx" "$sbom_spdx"
