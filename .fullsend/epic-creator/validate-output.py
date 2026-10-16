#!/usr/bin/env python3
"""Validate a Fullsend iteration's packaged epic-decompose output read-only."""

import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path

import jsonschema
import yaml


STRATEGY_ID = re.compile(r"^RHAISTRAT-\d+$")
START_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
EPIC_TYPES = {"Implementation", "Investigation"}
EPIC_PRIORITIES = {"P0", "P1", "P2"}
OPTIONS_WITH_VALUES = {"--jql", "--limit", "--batch-size", "--data-dir"}
TASK_FLAGS = {"--headless", "--announce-complete", "--reprocess", "--skip-if-has-epics"}
TASK_COMMANDS = {"/epic-creator:epic-decompose", "/epic-decompose"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _check_regular_tree(path):
    """Reject symlinks and special files before reading agent-writable output."""
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ValueError(f"missing output path: {path}") from exc
    _require(not stat.S_ISLNK(mode), f"symlink is not allowed: {path}")
    _require(stat.S_ISDIR(mode), f"expected directory: {path}")
    for entry in path.iterdir():
        mode = entry.lstat().st_mode
        _require(not stat.S_ISLNK(mode), f"symlink is not allowed: {entry}")
        if stat.S_ISDIR(mode):
            _check_regular_tree(entry)
        else:
            _require(stat.S_ISREG(mode), f"special file is not allowed: {entry}")


def _read_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    _require(bool(lines) and lines[0] == "---", f"missing YAML frontmatter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError(f"unterminated YAML frontmatter: {path}") from exc
    data = yaml.safe_load("\n".join(lines[1:end]))
    _require(isinstance(data, dict), f"frontmatter must be a mapping: {path}")
    body = "\n".join(lines[end + 1 :]).strip()
    _require(bool(body), f"empty artifact body: {path}")
    return data


def _read_yaml(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    _require(isinstance(data, dict), f"YAML document must be a mapping: {path}")
    return data


def _trusted_strategy_ids():
    task = os.environ.get("FULLSEND_TASK", "").strip()
    _require(bool(task), "trusted FULLSEND_TASK is missing")
    try:
        tokens = shlex.split(task)
    except ValueError as exc:
        raise ValueError("trusted FULLSEND_TASK has malformed quoting") from exc
    _require(bool(tokens) and tokens[0] in TASK_COMMANDS,
             "trusted FULLSEND_TASK must invoke epic-creator:epic-decompose")

    explicit_ids = []
    uses_jql = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in OPTIONS_WITH_VALUES:
            _require(index + 1 < len(tokens) and bool(tokens[index + 1]),
                     f"trusted FULLSEND_TASK is missing a value for {token}")
            uses_jql |= token == "--jql"
            index += 2
        elif token in TASK_FLAGS:
            index += 1
        elif STRATEGY_ID.fullmatch(token):
            explicit_ids.append(token)
            index += 1
        else:
            raise ValueError("trusted FULLSEND_TASK contains an unsupported argument")

    if uses_jql:
        _require(not explicit_ids, "provide either JQL or explicit IDs, not both")
        expected = os.environ.get("FULLSEND_EXPECTED_STRATEGY_IDS", "").split()
        _require(bool(expected),
                 "JQL runs require trusted FULLSEND_EXPECTED_STRATEGY_IDS for output validation")
    else:
        expected = explicit_ids
    _require(bool(expected), "trusted FULLSEND_TASK must include explicit IDs or JQL")
    _require(all(STRATEGY_ID.fullmatch(value) for value in expected),
             "trusted expected strategy IDs contain an invalid RHAISTRAT ID")
    _require(len(expected) == len(set(expected)), "trusted expected strategy IDs contain duplicates")
    return expected


def _nonempty_file(path):
    _require(path.is_file() and path.stat().st_size > 0, f"missing or empty artifact: {path}")


def _validate_epic_file(path, strategy_id):
    data = _read_frontmatter(path)
    required_fields = ("epic_id", "title", "parent_strat", "component", "team", "type", "priority")
    for field in required_fields:
        _require(isinstance(data.get(field), str),
                 f"{path.name}: required field {field} must be a string")
    _require(data["epic_id"] == path.stem,
             f"{path.name}: epic_id does not match filename")
    _require(data["parent_strat"] == strategy_id,
             f"{path.name}: parent_strat does not match selected strategy")
    _require(data["type"] in EPIC_TYPES,
             f"{path.name}: type must be Implementation or Investigation")
    _require(data["priority"] in EPIC_PRIORITIES,
             f"{path.name}: priority must be P0, P1, or P2")


def _validate_id_artifacts(artifacts, strategy_id):
    strategy = artifacts / "strat-tasks" / f"{strategy_id}.md"
    decomposition = artifacts / "epic-tasks" / f"{strategy_id}-decomposition.md"
    review = artifacts / "epic-reviews" / f"{strategy_id}-decomp-review.md"
    for path in (strategy, decomposition, review):
        _nonempty_file(path)

    summary = _read_frontmatter(decomposition)
    _require(summary.get("parent_strat") == strategy_id,
             f"{strategy_id}: decomposition parent_strat does not match")
    count = summary.get("epic_count")
    _require(isinstance(count, int) and not isinstance(count, bool) and count >= 0,
             f"{strategy_id}: decomposition epic_count must be a non-negative integer")
    critical_path = summary.get("critical_path_length")
    _require(isinstance(critical_path, int) and not isinstance(critical_path, bool)
             and critical_path >= 0,
             f"{strategy_id}: decomposition critical_path_length is malformed")
    _require(summary.get("decompose_complete") is True,
             f"{strategy_id}: decomposition is not marked complete")

    main_pattern = re.compile(re.escape(strategy_id) + r"-E\d+\.md$")
    branch_pattern = re.compile(re.escape(strategy_id) + r"-BRANCH-[A-Z]-E\d+\.md$")
    epic_files = [
        path for path in (artifacts / "epic-tasks").iterdir()
        if main_pattern.fullmatch(path.name) or branch_pattern.fullmatch(path.name)
    ]
    main_count = sum(bool(main_pattern.fullmatch(path.name)) for path in epic_files)
    _require(count in (main_count, len(epic_files)),
             f"{strategy_id}: epic_count {count} does not match {main_count} main-plan"
             f" or {len(epic_files)} total epic files")
    for path in epic_files:
        _validate_epic_file(path, strategy_id)

    review_data = _read_frontmatter(review)
    _require(review_data.get("strat_id") == strategy_id,
             f"{strategy_id}: review strat_id does not match")
    score = review_data.get("score")
    _require(isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 14,
             f"{strategy_id}: review score must be an integer from 0 to 14")
    _require(isinstance(review_data.get("pass"), bool),
             f"{strategy_id}: review pass must be boolean")
    _require(review_data.get("recommendation") in ("accept", "revise"),
             f"{strategy_id}: review recommendation is malformed")
    review_error = review_data.get("error")
    _require(review_error is None or isinstance(review_error, str),
             f"{strategy_id}: review error is malformed")
    _require(not review_error, f"{strategy_id}: review reports an error")
    return count, review_data["pass"]


def validate_output(result_dir, repo_dir, schema_path):
    _require(isinstance(repo_dir, (str, Path)) and bool(repo_dir),
             "TARGET_REPO_DIR is required")
    result_dir = Path(result_dir)
    repo_dir = Path(repo_dir)
    _require(repo_dir.is_absolute(), "source repository path must be absolute")
    try:
        canonical_repo = repo_dir.resolve(strict=True)
    except OSError as exc:
        raise ValueError("source repository is unavailable") from exc
    _require(canonical_repo.is_dir(), "source repository must be an existing directory")
    repo_dir = canonical_repo
    _check_regular_tree(result_dir)

    result_path = result_dir / "agent-result.json"
    _nonempty_file(result_path)
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        jsonschema.validate(instance=result, schema=schema)
    except (OSError, json.JSONDecodeError, jsonschema.SchemaError,
            jsonschema.ValidationError) as exc:
        raise ValueError(f"agent result schema validation failed: {exc}") from exc
    _require(result["action"] == "completed",
             "agent result action is failed; inspect its errors and resume the pipeline")

    # shortcut: sweep-only recovery has no repository snapshot; add retained per-iteration payloads before supporting it.
    artifacts = repo_dir / "artifacts"
    tmp = repo_dir / "tmp"
    _check_regular_tree(artifacts)
    _check_regular_tree(tmp)
    state_path = tmp / "pipeline-state.yaml"
    state = _read_yaml(state_path)
    _require(state.get("phase") == "DONE",
             f"pipeline phase is {state.get('phase', 'unknown')}, not DONE; resume with the existing epic-decompose dispatcher")
    started = state.get("start_time")
    _require(isinstance(started, str) and START_TIME.fullmatch(started),
             "pipeline start_time is missing or malformed")

    ids_path = tmp / "pipeline-all-ids.txt"
    _nonempty_file(ids_path)
    ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines()
           if line.strip()]
    _require(bool(ids), "pipeline-all-ids.txt contains no strategy IDs")
    _require(all(STRATEGY_ID.fullmatch(value) for value in ids),
             "pipeline-all-ids.txt contains an invalid RHAISTRAT ID")
    _require(len(ids) == len(set(ids)), "pipeline-all-ids.txt contains duplicate IDs")
    expected_ids = _trusted_strategy_ids()
    _require(set(ids) == set(expected_ids) and len(ids) == len(expected_ids),
             "packaged strategy IDs do not match trusted FULLSEND_TASK/env IDs")

    reports = artifacts / "decompose-runs"
    report = reports / (started.replace(":", "-") + ".yaml")
    _nonempty_file(report)
    run = _read_yaml(report)
    _require(run.get("started") == started, "run report does not match pipeline start_time")
    _require(isinstance(run.get("completed"), str) and bool(run["completed"]),
             "run report completion time is missing")
    total = run.get("total")
    _require(isinstance(total, int) and not isinstance(total, bool) and total == len(ids),
             "run report total does not match selected strategy IDs")
    results = run.get("results")
    _require(isinstance(results, list) and len(results) == len(ids),
             "run report results do not cover every selected strategy ID")

    reported = {}
    for item in results:
        _require(isinstance(item, dict), "run report contains a malformed result")
        strategy_id = item.get("strat_id")
        _require(isinstance(strategy_id, str) and strategy_id in ids
                 and strategy_id not in reported,
                 "run report contains an unexpected or duplicate strategy ID")
        epic_count, review_pass = _validate_id_artifacts(artifacts, strategy_id)
        reported_count = item.get("epic_count")
        _require(isinstance(reported_count, int) and not isinstance(reported_count, bool)
                 and reported_count == epic_count,
                 f"{strategy_id}: run report epic_count does not match decomposition")
        expected_status = "passed" if review_pass else "failed"
        _require(item.get("status") == expected_status,
                 f"{strategy_id}: run report status does not match review")
        reported[strategy_id] = item
    _require(set(reported) == set(ids), "run report is missing a selected strategy ID")


def main():
    schema_path = os.environ.get("FULLSEND_OUTPUT_SCHEMA")
    repo_dir = os.environ.get("TARGET_REPO_DIR")
    if not schema_path or not repo_dir:
        print("FAIL: FULLSEND_OUTPUT_SCHEMA and TARGET_REPO_DIR must be set", file=sys.stderr)
        return 1
    try:
        validate_output(Path.cwd() / "output", repo_dir, schema_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: epic-decompose output is complete and validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
