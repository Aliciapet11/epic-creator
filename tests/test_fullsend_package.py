import importlib.util
import inspect
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
FULLSEND = ROOT / ".fullsend"
VALIDATOR_PATH = FULLSEND / "epic-creator" / "validate-output.py"
SCHEMA_PATH = FULLSEND / "epic-creator" / "result.schema.json"
POST_PATH = FULLSEND / "epic-creator" / "post-epic-creator.py"

spec = importlib.util.spec_from_file_location("fullsend_validator", VALIDATOR_PATH)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
post_spec = importlib.util.spec_from_file_location("fullsend_postscript", POST_PATH)
postscript = importlib.util.module_from_spec(post_spec)
post_spec.loader.exec_module(postscript)


@pytest.fixture(autouse=True)
def trusted_task(monkeypatch):
    monkeypatch.setenv("FULLSEND_TASK", "/epic-decompose RHAISTRAT-9001")
    monkeypatch.delenv("FULLSEND_EXPECTED_STRATEGY_IDS", raising=False)


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_output(root, *, action="completed", phase="DONE", review_pass=True,
                 declared_count=1, actual_count=1, report=True):
    result_dir = root / "iteration-1" / "output"
    repo = root / "downloaded-repo"
    _write(result_dir / "agent-result.json", json.dumps({
        "action": action,
        "pipeline": "epic-decompose",
        "summary": "epic-decompose completed",
        "errors": [],
    }))
    started = "2026-10-16T12:00:00Z"
    _write(repo / "tmp" / "pipeline-state.yaml", yaml.safe_dump({
        "phase": phase,
        "start_time": started,
    }))
    _write(repo / "tmp" / "pipeline-all-ids.txt", "RHAISTRAT-9001\n")

    artifacts = repo / "artifacts"
    _write(artifacts / "strat-tasks" / "RHAISTRAT-9001.md", "Synthetic strategy\n")
    _write(artifacts / "epic-tasks" / "RHAISTRAT-9001-decomposition.md", (
        "---\nparent_strat: RHAISTRAT-9001\nepic_count: %d\n"
        "critical_path_length: 1\ndecompose_complete: true\n---\nSummary body\n"
    ) % declared_count)
    for index in range(1, actual_count + 1):
        epic_path = artifacts / "epic-tasks" / f"RHAISTRAT-9001-E{index:03}.md"
        epic_frontmatter = {
            "epic_id": epic_path.stem,
            "title": f"Epic {index}",
            "parent_strat": "RHAISTRAT-9001",
            "component": "component-a",
            "team": "team-a",
            "type": "Implementation",
            "priority": "P1",
        }
        _write(epic_path, "---\n" + yaml.safe_dump(epic_frontmatter, sort_keys=False)
               + "---\nEpic body\n")
    _write(artifacts / "epic-reviews" / "RHAISTRAT-9001-decomp-review.md", (
        "---\nstrat_id: RHAISTRAT-9001\nscore: 4\npass: %s\n"
        "recommendation: %s\n---\nReview body\n"
    ) % (str(review_pass).lower(), "accept" if review_pass else "revise"))
    if report:
        status = "passed" if review_pass else "failed"
        _write(artifacts / "decompose-runs" / "2026-10-16T12-00-00Z.yaml",
               yaml.safe_dump({
                   "started": started,
                   "completed": "2026-10-16T12:10:00Z",
                   "total": 1,
                   "results": [{"strat_id": "RHAISTRAT-9001",
                                "status": status,
                                "epic_count": declared_count}],
               }))
    return result_dir, repo


def test_package_config_registers_local_claude_agent_and_read_only_provider():
    config = yaml.safe_load((FULLSEND / "config.yaml").read_text(encoding="utf-8"))
    harness = yaml.safe_load((FULLSEND / "harness" / "epic-creator.yaml").read_text(encoding="utf-8"))
    assert config["version"] == "1"
    assert config["agents"] == [{
        "name": "epic-creator",
        "source": "harness/epic-creator.yaml",
        "runtime": "claude",
    }]
    assert config["agents"][0]["runtime"] == "claude"
    assert harness["providers"][0] == "epic-creator/jira-provider.yaml"
    assert harness["post_script"] == "epic-creator/post-epic-creator.py"
    assert harness["env"]["runner"]["JIRA_TOKEN"] == "${JIRA_TOKEN}"
    assert harness["env"]["runner"]["FULLSEND_TASK"] == "${FULLSEND_TASK}"
    assert harness["env"]["runner"]["FULLSEND_EXPECTED_STRATEGY_IDS"] == "${FULLSEND_EXPECTED_STRATEGY_IDS}"
    assert "JIRA_SERVER" not in harness["env"]["runner"]
    assert "JIRA_USER" not in harness["env"]["runner"]
    assert harness["providers"] == ["epic-creator/jira-provider.yaml", "vertex-ai"]
    assert harness["openshell"]["profiles"] == ["epic-creator/jira-profile.yaml"]
    profile = yaml.safe_load((FULLSEND / "epic-creator" / "jira-profile.yaml").read_text(encoding="utf-8"))
    assert profile["id"] == "epic-creator-jira"
    assert profile["endpoints"][0]["access"] == "read-only"
    assert profile["endpoints"][0]["enforcement"] == "enforce"
    assert profile["credentials"] == [{
        "name": "jira_token",
        "env_vars": ["JIRA_TOKEN"],
        "required": True,
        "auth_style": "basic",
    }]
    provider = yaml.safe_load((FULLSEND / "epic-creator" / "jira-provider.yaml").read_text(encoding="utf-8"))
    assert provider["name"] == "epic-creator-jira"
    assert provider["type"] == profile["id"]
    assert "JIRA_TOKEN" in provider["credentials"]


def test_prompt_contract_is_thin_and_native_skill_first():
    prompt = (FULLSEND / "epic-creator" / "prompt.md").read_text(encoding="utf-8")
    assert "call the `Skill` tool for `epic-creator:epic-decompose`" in prompt
    assert "passing only the parsed arguments after the command token" in prompt
    assert "only if the tool actually reports the skill unavailable" in prompt
    assert "resume the existing dispatcher in place" in prompt
    assert "its explicit `subagent_type` if present, otherwise `general-purpose`" in prompt
    assert "never use the `epic-creator` Fullsend wrapper as a phase worker" in prompt
    assert "write only `FULLSEND_OUTPUT_DIR/agent-result.json`" in prompt
    assert "Do not copy `artifacts/` or `tmp/`" in prompt
    assert "next-action" not in prompt and "wait-for-wave" not in prompt


def test_postscript_loads_validator_from_trusted_sibling_path():
    loaded = postscript._trusted_validator()
    assert Path(loaded.__file__).resolve() == VALIDATOR_PATH.resolve()


def _validate(result_dir, repo_dir):
    validator.validate_output(result_dir, repo_dir, SCHEMA_PATH)


def _seed_target(path, *, marker=True):
    path.mkdir(parents=True, exist_ok=True)
    if marker:
        (path / ".epic-fullsend-disposable").write_text("operator-approved\n", encoding="utf-8")
    _write(path / "keep.txt", "unrelated source\n")
    _write(path / "artifacts" / "preexisting.md", "replace only this tree\n")
    _write(path / "tmp" / "preexisting.txt", "replace only this tree\n")
    return path


def _snapshot_tree(path):
    snapshot = {}
    for entry in sorted(path.rglob("*")):
        relative = entry.relative_to(path).as_posix()
        if entry.is_symlink():
            snapshot[relative] = ("symlink", str(entry.readlink()))
        elif entry.is_dir():
            snapshot[relative] = ("directory", None)
        else:
            snapshot[relative] = ("file", entry.read_bytes())
    return snapshot


def test_completed_package_validates(tmp_path):
    result_dir, repo = _make_output(tmp_path)
    source = tmp_path / "source-worktree" / "artifacts" / "preserved.md"
    source.parent.mkdir(parents=True)
    source.write_text("preserve me\n", encoding="utf-8")

    _validate(result_dir, repo)

    assert source.read_text(encoding="utf-8") == "preserve me\n"
    assert tuple(inspect.signature(validator.validate_output).parameters) == (
        "result_dir", "repo_dir", "schema_path")


def test_validator_accepts_symlinked_source_parent(tmp_path):
    result_dir, repo = _make_output(tmp_path)
    alias = tmp_path / "download-alias"
    alias.symlink_to(repo.parent, target_is_directory=True)

    _validate(result_dir, alias / repo.name)


@pytest.mark.parametrize(("action", "phase", "message"), [
    ("failed", "DONE", "action is failed"),
    ("completed", "REVIEW_DECOMP", "not DONE"),
])
def test_failed_result_or_non_done_state_is_rejected(tmp_path, action, phase, message):
    result_dir, repo = _make_output(tmp_path, action=action, phase=phase)
    with pytest.raises(ValueError, match=message):
        _validate(result_dir, repo)


def test_missing_run_report_is_rejected(tmp_path):
    result_dir, repo = _make_output(tmp_path, report=False)
    with pytest.raises(ValueError, match="missing or empty artifact"):
        _validate(result_dir, repo)


@pytest.mark.parametrize("artifact", [
    "strat-tasks/RHAISTRAT-9001.md",
    "epic-tasks/RHAISTRAT-9001-decomposition.md",
    "epic-reviews/RHAISTRAT-9001-decomp-review.md",
])
def test_missing_key_artifact_is_rejected(tmp_path, artifact):
    result_dir, repo = _make_output(tmp_path)
    (repo / "artifacts" / artifact).unlink()
    with pytest.raises(ValueError, match="missing or empty artifact"):
        _validate(result_dir, repo)


def test_failed_business_review_is_a_valid_completed_result(tmp_path):
    result_dir, repo = _make_output(tmp_path, review_pass=False)
    _validate(result_dir, repo)


def test_jql_run_uses_trusted_expected_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("FULLSEND_TASK", '/epic-decompose --jql "project = RHAI"')
    monkeypatch.setenv("FULLSEND_EXPECTED_STRATEGY_IDS", "RHAISTRAT-9001")
    result_dir, repo = _make_output(tmp_path)
    _validate(result_dir, repo)


def test_canonical_namespaced_task_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("FULLSEND_TASK", "/epic-creator:epic-decompose RHAISTRAT-9001")
    _validate(*_make_output(tmp_path))


def test_unrelated_task_command_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("FULLSEND_TASK", "/another-plugin:epic-decompose RHAISTRAT-9001")
    with pytest.raises(ValueError, match="must invoke epic-creator:epic-decompose"):
        _validate(*_make_output(tmp_path))


def test_packaged_ids_must_match_trusted_task(tmp_path):
    result_dir, repo = _make_output(tmp_path)
    _write(repo / "tmp" / "pipeline-all-ids.txt", "RHAISTRAT-9002\n")
    with pytest.raises(ValueError, match="do not match trusted FULLSEND_TASK/env IDs"):
        _validate(result_dir, repo)


@pytest.mark.parametrize("status", ["error", "missing"])
def test_run_report_error_or_missing_entry_is_rejected(tmp_path, status):
    result_dir, repo = _make_output(tmp_path)
    report = repo / "artifacts" / "decompose-runs" / "2026-10-16T12-00-00Z.yaml"
    data = yaml.safe_load(report.read_text(encoding="utf-8"))
    data["results"][0]["status"] = status
    report.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="run report status does not match review"):
        _validate(result_dir, repo)


def test_declared_epic_count_must_match_epic_files(tmp_path):
    result_dir, repo = _make_output(tmp_path, declared_count=2, actual_count=1)
    with pytest.raises(ValueError, match="epic_count 2 does not match"):
        _validate(result_dir, repo)


@pytest.mark.parametrize("mutation, message", [
    ("no-frontmatter", "missing YAML frontmatter"),
    ("missing-required", "required field component must be a string"),
    ("mismatched-epic-id", "epic_id does not match filename"),
    ("mismatched-parent", "parent_strat does not match selected strategy"),
    ("invalid-enum", "type must be Implementation or Investigation"),
    ("non-string", "required field team must be a string"),
])
def test_epic_frontmatter_is_validated(tmp_path, mutation, message):
    result_dir, repo = _make_output(tmp_path)
    epic = repo / "artifacts" / "epic-tasks" / "RHAISTRAT-9001-E001.md"
    data = yaml.safe_load(epic.read_text(encoding="utf-8").split("---\n", 2)[1])
    if mutation == "no-frontmatter":
        epic.write_text("Epic body only\n", encoding="utf-8")
    else:
        if mutation == "missing-required":
            del data["component"]
        elif mutation == "mismatched-epic-id":
            data["epic_id"] = "RHAISTRAT-9001-E999"
        elif mutation == "mismatched-parent":
            data["parent_strat"] = "RHAISTRAT-9002"
        elif mutation == "invalid-enum":
            data["type"] = "Other"
        elif mutation == "non-string":
            data["team"] = ["team-a"]
        epic.write_text("---\n" + yaml.safe_dump(data, sort_keys=False)
                        + "---\nEpic body\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _validate(result_dir, repo)


@pytest.mark.parametrize("score", [-1, 15])
def test_review_score_must_be_between_zero_and_fourteen(tmp_path, score):
    result_dir, repo = _make_output(tmp_path)
    review = repo / "artifacts" / "epic-reviews" / "RHAISTRAT-9001-decomp-review.md"
    review.write_text(
        f"---\nstrat_id: RHAISTRAT-9001\nscore: {score}\npass: true\n"
        "recommendation: accept\n---\nReview body\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="review score must be an integer from 0 to 14"):
        _validate(result_dir, repo)


def test_symlinked_payload_is_rejected(tmp_path):
    result_dir, repo = _make_output(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("phase: DONE\n", encoding="utf-8")
    (repo / "tmp" / "pipeline-state.yaml").unlink()
    (repo / "tmp" / "pipeline-state.yaml").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink is not allowed"):
        _validate(result_dir, repo)


def test_sweep_without_target_repo_fails_closed(tmp_path, monkeypatch):
    result_dir, repo = _make_output(tmp_path)
    before = _snapshot_tree(repo)
    monkeypatch.chdir(result_dir.parent)
    monkeypatch.setenv("FULLSEND_OUTPUT_SCHEMA", str(SCHEMA_PATH))
    monkeypatch.delenv("TARGET_REPO_DIR", raising=False)

    assert validator.main() == 1
    assert _snapshot_tree(repo) == before


def test_postscript_revalidates_then_copies_only_artifacts_and_tmp(tmp_path):
    result_dir, repo = _make_output(tmp_path)
    target = _seed_target(tmp_path / "disposable")

    postscript.promote_output(result_dir, str(repo), SCHEMA_PATH, str(target))

    assert (target / "artifacts" / "epic-tasks" / "RHAISTRAT-9001-E001.md").is_file()
    assert (target / "tmp" / "pipeline-state.yaml").is_file()
    assert (target / ".epic-fullsend-disposable").is_file()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "unrelated source\n"
    assert not (result_dir / "artifacts").exists()
    assert not (result_dir / "tmp").exists()


def test_postscript_accepts_symlinked_download_source_parent(tmp_path):
    result_dir, repo = _make_output(tmp_path)
    target = _seed_target(tmp_path / "disposable")
    alias = tmp_path / "download-alias"
    alias.symlink_to(repo.parent, target_is_directory=True)

    postscript.promote_output(result_dir, str(alias / repo.name), SCHEMA_PATH, str(target))

    assert (target / "artifacts" / "epic-tasks" / "RHAISTRAT-9001-E001.md").is_file()


@pytest.mark.parametrize("failure", ["failed-result", "incomplete-state", "malformed-epic", "missing-repo"])
def test_postscript_validation_failures_do_not_modify_target(tmp_path, failure):
    result_dir, repo = _make_output(tmp_path)
    target = _seed_target(tmp_path / "disposable")
    if failure == "failed-result":
        _write(result_dir / "agent-result.json", json.dumps({
            "action": "failed", "pipeline": "epic-decompose", "summary": "failed", "errors": ["test"]
        }))
    elif failure == "incomplete-state":
        _write(repo / "tmp" / "pipeline-state.yaml", "phase: REVIEW_DECOMP\nstart_time: 2026-10-16T12:00:00Z\n")
    elif failure == "malformed-epic":
        _write(repo / "artifacts" / "epic-tasks" / "RHAISTRAT-9001-E001.md", "Epic without metadata\n")
    else:
        repo = ""
    before = _snapshot_tree(target)

    with pytest.raises(ValueError):
        postscript.promote_output(result_dir, str(repo) if repo else "", SCHEMA_PATH, str(target))

    assert _snapshot_tree(target) == before


@pytest.mark.parametrize("failure", ["empty-target", "missing-marker"])
def test_postscript_target_guards_do_not_modify_target(tmp_path, failure):
    result_dir, repo = _make_output(tmp_path)
    target = _seed_target(tmp_path / "disposable", marker=failure != "missing-marker")
    before = _snapshot_tree(target)

    with pytest.raises(ValueError):
        postscript.promote_output(result_dir, str(repo), SCHEMA_PATH,
                                  "" if failure == "empty-target" else str(target))

    assert _snapshot_tree(target) == before


def test_postscript_rejects_symlinked_target_component_without_modifying_target(tmp_path):
    result_dir, repo = _make_output(tmp_path)
    real_parent = tmp_path / "real-parent"
    target = _seed_target(real_parent / "disposable")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    before = _snapshot_tree(target)

    with pytest.raises(ValueError, match="symlink component"):
        postscript.promote_output(result_dir, str(repo), SCHEMA_PATH,
                                  str(linked_parent / "disposable"))

    assert _snapshot_tree(target) == before


def test_postscript_rejects_source_package_checkout_without_modifying_it(tmp_path):
    result_dir, repo = _make_output(tmp_path)
    package_readme = ROOT / "CLAUDE.md"
    before = package_readme.read_bytes()

    with pytest.raises(ValueError, match="source/package checkout"):
        postscript.promote_output(result_dir, str(repo), SCHEMA_PATH, str(ROOT))

    assert package_readme.read_bytes() == before


def test_postscript_rejects_downloaded_repo_as_target_without_modifying_it(tmp_path):
    result_dir, repo = _make_output(tmp_path)
    (repo / ".epic-fullsend-disposable").write_text("operator-approved\n", encoding="utf-8")
    before = _snapshot_tree(repo)

    with pytest.raises(ValueError, match="downloaded repository"):
        postscript.promote_output(result_dir, str(repo), SCHEMA_PATH, str(repo))

    assert _snapshot_tree(repo) == before


def test_pre_script_requires_operator_marker_before_bootstrap(tmp_path, monkeypatch):
    target = tmp_path / "disposable"
    target.mkdir()
    monkeypatch.setenv("TARGET_REPO_DIR", str(target))

    result = subprocess.run(["bash", str(FULLSEND / "epic-creator" / "pre-epic-creator.sh")],
                            capture_output=True, text=True)

    assert result.returncode != 0
    assert "create .epic-fullsend-disposable" in result.stderr
    assert not (target / ".context").exists()


def test_plugin_staging_copies_current_sources_and_hook(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".fullsend").mkdir()
    shutil.copy2(FULLSEND / "prepare-plugin.sh", checkout / ".fullsend" / "prepare-plugin.sh")
    for name in (".claude-plugin", "skills", "scripts", "hooks"):
        shutil.copytree(ROOT / name, checkout / name)

    current_skill = checkout / "skills" / "epic-decompose" / "SKILL.md"
    current_skill.write_text(current_skill.read_text(encoding="utf-8") + "\ncurrent source marker\n",
                             encoding="utf-8")
    subprocess.run(["bash", str(checkout / ".fullsend" / "prepare-plugin.sh")],
                   check=True, capture_output=True, text=True)

    staged = checkout / ".fullsend" / "plugins" / "epic-creator"
    assert "current source marker" in (staged / "skills" / "epic-decompose" / "SKILL.md").read_text(encoding="utf-8")
    assert (staged / "hooks" / "hooks.json").is_file()
    assert (staged / "scripts" / "pipeline_state.py").is_file()
    assert (staged / ".claude-plugin" / "plugin.json").is_file()


def test_plugin_staging_refuses_symlinked_destination(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".fullsend").mkdir()
    shutil.copy2(FULLSEND / "prepare-plugin.sh", checkout / ".fullsend" / "prepare-plugin.sh")
    for name in (".claude-plugin", "skills", "scripts", "hooks"):
        shutil.copytree(ROOT / name, checkout / name)
    plugin_parent = checkout / ".fullsend" / "plugins"
    plugin_parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (plugin_parent / "epic-creator").symlink_to(outside, target_is_directory=True)

    result = subprocess.run(["bash", str(checkout / ".fullsend" / "prepare-plugin.sh")],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "symlinked staging path" in result.stderr
    assert list(outside.iterdir()) == []
