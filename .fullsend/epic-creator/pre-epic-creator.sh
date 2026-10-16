#!/usr/bin/env bash
set -euo pipefail

: "${TARGET_REPO_DIR:?TARGET_REPO_DIR must be set}"
PACKAGE_ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
python3 - "${TARGET_REPO_DIR}" "${PACKAGE_ROOT}" <<'PY'
import stat
import sys
from pathlib import Path

target = Path(sys.argv[1])
package = Path(sys.argv[2]).resolve(strict=True)
if not target.is_absolute():
    raise SystemExit("TARGET_REPO_DIR must be absolute")
current = Path(target.anchor)
for part in target.parts[1:]:
    current /= part
    if current.is_symlink():
        raise SystemExit("TARGET_REPO_DIR has a symlink component")
try:
    canonical = target.resolve(strict=True)
except OSError as exc:
    raise SystemExit(f"TARGET_REPO_DIR is unavailable: {exc}")
if target != canonical or not canonical.is_dir():
    raise SystemExit("TARGET_REPO_DIR must be a canonical directory")
if canonical == package or canonical in package.parents or package in canonical.parents:
    raise SystemExit("refusing to bootstrap the source/package checkout")
marker = canonical / ".epic-fullsend-disposable"
try:
    mode = marker.lstat().st_mode
except OSError:
    raise SystemExit("create .epic-fullsend-disposable in the disposable target first")
if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
    raise SystemExit(".epic-fullsend-disposable must be a regular, non-symlink file")
PY

cd -- "${TARGET_REPO_DIR}"
bash scripts/fetch-architecture-context.sh
python3 scripts/fetch_components.py
