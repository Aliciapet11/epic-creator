#!/usr/bin/env python3
"""Revalidate Fullsend output and promote artifacts into an approved disposable checkout."""

import importlib.util
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _trusted_validator():
    path = Path(__file__).resolve().with_name("validate-output.py")
    _require(path.is_file(), "trusted validator is missing beside the post-script")
    spec = importlib.util.spec_from_file_location("epic_creator_trusted_validator", path)
    _require(spec is not None and spec.loader is not None,
             "could not load the trusted package validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_directory(value, name):
    _require(isinstance(value, str) and bool(value), f"{name} is required")
    path = Path(value)
    _require(path.is_absolute(), f"{name} must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        _require(not current.is_symlink(), f"{name} has a symlink component")
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{name} is unavailable") from exc
    _require(path == canonical and canonical.is_dir(),
             f"{name} must be a canonical directory")
    return canonical


def _source_directory(value, name):
    _require(isinstance(value, (str, Path)) and bool(value), f"{name} is required")
    path = Path(value)
    _require(path.is_absolute(), f"{name} must be absolute")
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{name} is unavailable") from exc
    _require(canonical.is_dir(), f"{name} must be an existing directory")
    return canonical


def _overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def promote_output(result_dir, repo_dir, schema_path, target_dir):
    _require(bool(repo_dir), "REPO_DIR is required; no downloaded repository was extracted")
    _require(bool(schema_path), "FULLSEND_OUTPUT_SCHEMA is required")
    repo = _source_directory(repo_dir, "REPO_DIR")
    validator = _trusted_validator()
    validator.validate_output(result_dir, repo, schema_path)

    target = _canonical_directory(target_dir, "TARGET_REPO_DIR")
    package = Path(__file__).resolve().parents[2]
    _require(not _overlaps(target, package),
             "refusing to modify the source/package checkout")
    _require(not _overlaps(target, repo),
             "refusing to modify the downloaded repository")

    marker = target / ".epic-fullsend-disposable"
    try:
        marker_mode = marker.lstat().st_mode
    except OSError as exc:
        raise ValueError("create .epic-fullsend-disposable in the disposable target first") from exc
    _require(stat.S_ISREG(marker_mode) and not stat.S_ISLNK(marker_mode),
             ".epic-fullsend-disposable must be a regular, non-symlink file")

    destinations = {name: target / name for name in ("artifacts", "tmp")}
    for name, path in destinations.items():
        _require(not path.is_symlink(), f"refusing symlinked target {name}/")
        _require(not path.exists() or path.is_dir(),
                 f"target {name}/ exists and is not a directory")

    with tempfile.TemporaryDirectory(prefix=".epic-fullsend-copy-", dir=target) as staging:
        staging = Path(staging)
        for name in destinations:
            shutil.copytree(repo / name, staging / name, copy_function=shutil.copy2)
        for name, destination in destinations.items():
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(staging / name, destination)


def main():
    try:
        result_dir = os.environ.get("FULLSEND_VALIDATED_ITERATION_DIR")
        repo_dir = os.environ.get("REPO_DIR")
        target_dir = os.environ.get("TARGET_REPO_DIR")
        schema_path = os.environ.get("FULLSEND_OUTPUT_SCHEMA")
        _require(bool(result_dir), "FULLSEND_VALIDATED_ITERATION_DIR is required")
        _require(bool(schema_path), "FULLSEND_OUTPUT_SCHEMA is required")
        promote_output(result_dir, repo_dir, schema_path, target_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Promoted validated artifacts/ and tmp/ to the disposable target")
    return 0


if __name__ == "__main__":
    sys.exit(main())
