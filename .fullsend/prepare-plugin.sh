#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
FULLSEND_DIR="${ROOT}/.fullsend"
PLUGIN_PARENT="${FULLSEND_DIR}/plugins"
DEST="${PLUGIN_PARENT}/epic-creator"

for path in "${FULLSEND_DIR}" "${PLUGIN_PARENT}" "${DEST}"; do
  if [[ -L "${path}" ]]; then
    echo "Refusing symlinked staging path: ${path}" >&2
    exit 1
  fi
done
if [[ -e "${DEST}" ]]; then
  echo "Staging destination already exists; remove it explicitly before restaging" >&2
  exit 1
fi

for name in .claude-plugin skills scripts hooks; do
  source="${ROOT}/${name}"
  if [[ -L "${source}" || ! -d "${source}" ]]; then
    echo "Missing or symlinked plugin source directory: ${name}" >&2
    exit 1
  fi
  if find "${source}" -type l -print -quit | grep -q .; then
    echo "Plugin source contains a symlink: ${name}" >&2
    exit 1
  fi
done

mkdir -p -- "${PLUGIN_PARENT}"
mkdir -- "${DEST}"
for name in .claude-plugin skills scripts hooks; do
  cp -R -- "${ROOT}/${name}" "${DEST}/"
done
echo "Staged the native epic-creator plugin under .fullsend/plugins/epic-creator"
