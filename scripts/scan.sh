#!/usr/bin/env bash
set -euo pipefail
umask 077

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
app_image=${WAYSPLIT_SCAN_IMAGE:-waysplit:scan}
trivy_image='aquasec/trivy:0.73.0@sha256:7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c'
fs_report="$repo_root/trivy-fs.json"
image_report="$repo_root/trivy-image.json"
scan_status=0

# Scan only version-control candidates. Ignored statements, .env files, SQLite,
# uploads, and prior reports never enter the scanner's source mount.
mkdir -p "$repo_root/data"
scan_tmp=$(mktemp -d "$repo_root/data/.waysplit-scan.XXXXXX")
trap 'rm -rf -- "$scan_tmp"' EXIT
source_stage="$scan_tmp/source"
mkdir -p "$source_stage"
while IFS= read -r -d '' source_file; do
  destination="$source_stage/$source_file"
  mkdir -p -- "$(dirname -- "$destination")"
  cp -P -- "$repo_root/$source_file" "$destination"
done < <(git -C "$repo_root" ls-files -z --cached --others --exclude-standard)

docker build --tag "$app_image" "$repo_root"

if command -v trivy >/dev/null 2>&1; then
  trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL \
    --format json --output "$fs_report" "$source_stage"
  trivy image --scanners vuln,secret --severity HIGH,CRITICAL \
    --format json --output "$image_report" "$app_image"

  trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL \
    --exit-code 1 "$source_stage" || scan_status=1
  trivy image --scanners vuln,secret --severity HIGH,CRITICAL \
    --exit-code 1 "$app_image" || scan_status=1
else
  mkdir -p "$scan_tmp/cache" "$scan_tmp/checks-warmup"
  docker save --output "$scan_tmp/image.tar" "$app_image"

  # Fetch vulnerability and misconfiguration policy data before the
  # no-network source scanner can see even the clean staged tree. The policy
  # warm-up scans an empty directory; source never enters a networked scanner.
  docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev \
    --volume "$scan_tmp:/work" "$trivy_image" image --cache-dir /work/cache \
    --download-db-only
  docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev \
    --volume "$scan_tmp:/work" "$trivy_image" fs --cache-dir /work/cache \
    --scanners misconfig --severity HIGH,CRITICAL /work/checks-warmup >/dev/null

  docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev \
    --volume "$source_stage:/workspace:ro" --volume "$scan_tmp:/work" \
    "$trivy_image" fs --cache-dir /work/cache --scanners vuln,secret,misconfig \
    --skip-db-update --skip-check-update --severity HIGH,CRITICAL --format json \
    --output /work/trivy-fs.json /workspace
  docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev \
    --volume "$scan_tmp:/work" \
    "$trivy_image" image --input /work/image.tar --cache-dir /work/cache \
    --scanners vuln,secret --severity HIGH,CRITICAL \
    --format json --output /work/trivy-image.json

  docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev \
    --volume "$source_stage:/workspace:ro" --volume "$scan_tmp:/work" \
    "$trivy_image" fs --cache-dir /work/cache --scanners vuln,secret,misconfig \
    --skip-db-update --skip-check-update --severity HIGH,CRITICAL --exit-code 1 \
    /workspace || scan_status=1
  docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev \
    --volume "$scan_tmp:/work" \
    "$trivy_image" image --input /work/image.tar --cache-dir /work/cache \
    --scanners vuln,secret --severity HIGH,CRITICAL --exit-code 1 \
    || scan_status=1

  install -m 0600 "$scan_tmp/trivy-fs.json" "$fs_report"
  install -m 0600 "$scan_tmp/trivy-image.json" "$image_report"
fi

printf 'Reports: %s and %s\n' "$fs_report" "$image_report"
printf 'Treat JSON reports as sensitive evidence even though private runtime paths were excluded.\n'
exit "$scan_status"
