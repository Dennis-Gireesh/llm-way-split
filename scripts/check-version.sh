#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
python_bin=${PYTHON_BIN:-python3}
expected_tag=${1:-}

versions=$(
  "$python_bin" - "$repo_root" <<'PY'
import ast
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
with (root / "pyproject.toml").open("rb") as handle:
    project_version = tomllib.load(handle)["project"]["version"]

tree = ast.parse((root / "src/waysplit/__init__.py").read_text(encoding="utf-8"))
module_version = None
for node in tree.body:
    if not isinstance(node, ast.Assign):
        continue
    if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            module_version = node.value.value
            break

if module_version is None:
    raise SystemExit("src/waysplit/__init__.py does not define a literal __version__")

print(f"{project_version}\t{module_version}")
PY
)

project_version=${versions%%$'\t'*}
module_version=${versions#*$'\t'}

if [[ "$project_version" != "$module_version" ]]; then
  printf 'Version mismatch: pyproject.toml=%s, waysplit.__version__=%s\n' \
    "$project_version" "$module_version" >&2
  exit 1
fi

if [[ ! "$project_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]]; then
  printf 'Project version is not release-compatible SemVer: %s\n' "$project_version" >&2
  exit 1
fi

if [[ -n "$expected_tag" ]]; then
  expected_tag=${expected_tag#refs/tags/}
  if [[ "$expected_tag" != "v$project_version" ]]; then
    printf 'Release tag must be v%s, received %s\n' "$project_version" "$expected_tag" >&2
    exit 1
  fi
fi

printf 'Version verified: %s\n' "$project_version"
