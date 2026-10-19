import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
FULLSEND = ROOT / ".fullsend"
HARNESS = FULLSEND / "epic-creator" / "epic-creator.yaml"
SCHEMA = FULLSEND / "epic-creator" / "result.schema.json"
VALIDATOR = FULLSEND / "epic-creator" / "validate-output-schema.sh"
PRE = FULLSEND / "epic-creator" / "pre-epic-creator.sh"
POST = FULLSEND / "epic-creator" / "post-epic-creator.sh"


def _environment(tmp_path, **extra):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "LANG": "C",
    }
    env.update({key: str(value) for key, value in extra.items()})
    return env


def _fixture(iteration, *, phase="DONE", report=True):
    output = iteration / "output"
    output.mkdir(parents=True)
    (output / "agent-result.json").write_text(
        json.dumps({
            "action": "completed",
            "pipeline": "epic-decompose",
            "summary": "Synthetic completed run",
            "errors": [],
        }) + "\n",
        encoding="utf-8",
    )
    repo = iteration.parent / "source-repo"
    (repo / "tmp").mkdir(parents=True)
    (repo / "tmp" / "pipeline-state.yaml").write_text(
        f"phase: {phase}\n", encoding="utf-8"
    )
    runs = repo / "artifacts" / "decompose-runs"
    runs.mkdir(parents=True)
    if report:
        (runs / "report.yaml").write_text(
            "review:\n  pass: false\n", encoding="utf-8"
        )
    return output, repo


def _run_validator(iteration, repo=None, **extra):
    values = {"FULLSEND_OUTPUT_SCHEMA": SCHEMA, **extra}
    if repo is not None:
        values["TARGET_REPO_DIR"] = repo
    return subprocess.run(
        ["bash", str(VALIDATOR)],
        cwd=iteration,
        env=_environment(iteration, **values),
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_package_configuration():
    config = yaml.safe_load((FULLSEND / "config.yaml").read_text(encoding="utf-8"))
    assert config["version"] == "1"
    assert config["agents"] == [{"source": "epic-creator/epic-creator.yaml"}]
    harness = yaml.safe_load(HARNESS.read_text(encoding="utf-8"))
    assert harness["providers"] == ["epic-creator/jira-provider.yaml", "vertex-ai"]
    assert harness["openshell"]["profiles"] == ["epic-creator/jira-profile.yaml"]
    assert harness["validation_loop"]["script"] == "epic-creator/validate-output-schema.sh"
    assert harness["pre_script"] == "epic-creator/pre-epic-creator.sh"
    assert harness["post_script"] == "epic-creator/post-epic-creator.sh"
    assert harness["model"] == "claude-opus-4-6"
    for key in ("JIRA_SERVER", "JIRA_USER", "JIRA_TOKEN", "FULLSEND_TASK"):
        assert harness["env"]["runner"][key] == "${" + key + "}"
    assert "FULLSEND_EXPECTED_STRATEGY_IDS" not in harness["env"]["runner"]
    assert harness["env"]["sandbox"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "claude-opus-4-6"

    profile = yaml.safe_load(
        (FULLSEND / harness["openshell"]["profiles"][0]).read_text(encoding="utf-8")
    )
    assert profile["id"] == "epic-creator-jira"
    assert profile["endpoints"][0]["host"] == "redhat.atlassian.net"
    assert profile["endpoints"][0]["access"] == "read-only"
    assert profile["endpoints"][0]["enforcement"] == "enforce"
    assert profile["credentials"] == [{
        "name": "jira_token",
        "env_vars": ["JIRA_TOKEN"],
        "required": True,
        "auth_style": "basic",
    }]
    provider = yaml.safe_load(
        (FULLSEND / harness["providers"][0]).read_text(encoding="utf-8")
    )
    assert provider["name"] == "epic-creator-jira"
    assert provider["type"] == "epic-creator-jira"
    assert "JIRA_TOKEN" in provider["credentials"]


def test_native_skill_prompt_contract():
    prompt = (FULLSEND / "epic-creator" / "prompt.md").read_text(encoding="utf-8")
    for phrase in (
        "call the `Skill` tool for `epic-creator:epic-decompose`",
        "passing only the parsed arguments after the command token",
        "only if the tool actually reports the skill unavailable",
        "resume the existing dispatcher in place",
        "its explicit `subagent_type` if present, otherwise `general-purpose`",
        "never use the `epic-creator` Fullsend wrapper as a phase worker",
        "write only `FULLSEND_OUTPUT_DIR/agent-result.json`",
        "Do not copy `artifacts/` or `tmp/`",
    ):
        assert phrase in prompt


def test_validator_accepts_completed_run_with_failed_business_review(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    result = _run_validator(iteration, repo)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "PASS: pipeline phase DONE" in result.stdout
    assert sorted(path.name for path in output.iterdir()) == ["agent-result.json"]


@pytest.mark.parametrize("case", [
    "invalid-json", "wrong-pipeline", "failed-action", "missing-result",
    "missing-output-directory", "missing-state", "missing-target", "non-done",
    "empty-report-directory",
])
def test_validator_rejects_invalid_or_incomplete_run(tmp_path, case):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    result_file = output / "agent-result.json"
    if case == "invalid-json":
        result_file.write_text("{not json\n", encoding="utf-8")
    elif case in ("wrong-pipeline", "failed-action"):
        data = json.loads(result_file.read_text(encoding="utf-8"))
        data["pipeline" if case == "wrong-pipeline" else "action"] = (
            "wrong" if case == "wrong-pipeline" else "failed"
        )
        result_file.write_text(json.dumps(data) + "\n", encoding="utf-8")
    elif case == "missing-result":
        result_file.unlink()
    elif case == "missing-output-directory":
        shutil.rmtree(output)
    elif case == "missing-state":
        (repo / "tmp" / "pipeline-state.yaml").unlink()
    elif case == "non-done":
        (repo / "tmp" / "pipeline-state.yaml").write_text(
            "phase: RUNNING\n", encoding="utf-8"
        )
    elif case == "empty-report-directory":
        for path in (repo / "artifacts" / "decompose-runs").iterdir():
            path.unlink()
    result = _run_validator(iteration, None if case == "missing-target" else repo)
    assert result.returncode != 0


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
def test_validator_rejects_unsafe_result_file_without_blocking(tmp_path, unsafe_kind):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    result_file = output / "agent-result.json"
    result_file.unlink()
    if unsafe_kind == "symlink":
        private_file = tmp_path / "private.json"
        private_file.write_text("{}\n", encoding="utf-8")
        result_file.symlink_to(private_file)
    else:
        os.mkfifo(result_file)
    assert _run_validator(iteration, repo).returncode != 0


@pytest.mark.parametrize("unsafe_kind", ["symlink", "fifo"])
@pytest.mark.parametrize("payload_dir", ["artifacts", "tmp"])
def test_validator_rejects_unsafe_payload_entries(tmp_path, unsafe_kind, payload_dir):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration)
    directory = repo / payload_dir
    if unsafe_kind == "symlink":
        private_file = tmp_path / "private-payload"
        private_file.write_text("private\n", encoding="utf-8")
        (directory / "untrusted").symlink_to(private_file)
    else:
        os.mkfifo(directory / "untrusted")
    assert _run_validator(iteration, repo).returncode != 0


def test_validator_uses_basename_for_custom_output_file(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    (output / "custom.json").write_text(
        (output / "agent-result.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = _run_validator(iteration, repo, FULLSEND_OUTPUT_FILE="../custom.json")
    assert result.returncode == 0, result.stderr + result.stdout


def _post(iteration, output, repo, target, *, path=None):
    env = _environment(
        iteration,
        FULLSEND_VALIDATED_ITERATION_DIR=output,
        REPO_DIR=repo,
        TARGET_REPO_DIR=target,
    )
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        ["bash", str(POST)], cwd=iteration, env=env,
        capture_output=True, text=True, timeout=10,
    )


def _disposable_target(path):
    path.mkdir()
    (path / "keep.txt").write_text("preserve\n", encoding="utf-8")
    (path / "artifacts").mkdir()
    (path / "artifacts" / "old.txt").write_text("replace\n", encoding="utf-8")
    (path / "tmp").mkdir()
    (path / "tmp" / "old.txt").write_text("replace\n", encoding="utf-8")
    return path


def test_post_copies_validated_repository_trees_to_disposable_target(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    assert _run_validator(iteration, repo).returncode == 0
    target = _disposable_target(tmp_path / "disposable")
    result = _post(iteration, output, repo, target)
    assert result.returncode == 0, result.stderr + result.stdout
    report = Path("artifacts/decompose-runs/report.yaml")
    state = Path("tmp/pipeline-state.yaml")
    for relative in (report, state):
        assert (target / relative).read_bytes() == (repo / relative).read_bytes()
    assert not (target / "artifacts" / "old.txt").exists()
    assert not (target / "tmp" / "old.txt").exists()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "preserve\n"
    assert not (target / "agent-result.json").exists()
    assert sorted(path.name for path in output.iterdir()) == ["agent-result.json"]
    assert (repo / report).is_file()


def test_post_copy_failure_propagates_without_claiming_rollback(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    assert _run_validator(iteration, repo).returncode == 0
    target = _disposable_target(tmp_path / "disposable")
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "cp"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    result = _post(
        iteration, output, repo, target,
        path=f"{stub_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
    )
    assert result.returncode != 0
    assert (target / "keep.txt").read_text(encoding="utf-8") == "preserve\n"
    assert (repo / "artifacts" / "decompose-runs" / "report.yaml").is_file()


def test_pre_runs_only_target_bootstrap_scripts(tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "fetch-architecture-context.sh").write_text(
        "#!/usr/bin/env bash\ntouch architecture-called\n", encoding="utf-8"
    )
    (scripts / "fetch_components.py").write_text(
        "from pathlib import Path\nPath('components-called').touch()\n", encoding="utf-8"
    )
    result = subprocess.run(
        ["bash", str(PRE)], cwd=tmp_path,
        env=_environment(tmp_path, TARGET_REPO_DIR=checkout),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert (checkout / "architecture-called").is_file()
    assert (checkout / "components-called").is_file()
    missing = subprocess.run(
        ["bash", str(PRE)], cwd=tmp_path, env=_environment(tmp_path),
        capture_output=True, text=True, timeout=10,
    )
    assert missing.returncode != 0


def _plugin_checkout(path):
    checkout = path / "checkout"
    (checkout / ".fullsend").mkdir(parents=True)
    shutil.copy2(FULLSEND / "prepare-plugin.sh", checkout / ".fullsend")
    files = {
        ".claude-plugin/plugin.json": "{}\n",
        "skills/example/SKILL.md": "synthetic skill\n",
        "scripts/example.sh": "#!/usr/bin/env bash\n",
        "hooks/hook.json": "{}\n",
    }
    for relative, content in files.items():
        source = checkout / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(content, encoding="utf-8")
    (checkout / "private.env").write_text("private sentinel\n", encoding="utf-8")
    return checkout, files


def _run_plugin_stager(checkout, tmp_path):
    return subprocess.run(
        ["bash", str(checkout / ".fullsend" / "prepare-plugin.sh")],
        cwd=checkout, env=_environment(tmp_path),
        capture_output=True, text=True, timeout=10,
    )


def test_plugin_staging_copies_only_selected_trees(tmp_path):
    checkout, files = _plugin_checkout(tmp_path)
    result = _run_plugin_stager(checkout, tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    destination = checkout / ".fullsend" / "plugins" / "epic-creator"
    for relative, content in files.items():
        assert (destination / relative).read_text(encoding="utf-8") == content
    assert not (destination / "private.env").exists()


def test_plugin_staging_rejects_top_level_source_symlink(tmp_path):
    checkout, _ = _plugin_checkout(tmp_path)
    skills = checkout / "skills"
    shutil.rmtree(skills)
    outside = tmp_path / "external-skills"
    outside.mkdir()
    (outside / "SKILL.md").write_text("external\n", encoding="utf-8")
    skills.symlink_to(outside, target_is_directory=True)
    assert _run_plugin_stager(checkout, tmp_path).returncode != 0
    assert not (checkout / ".fullsend" / "plugins" / "epic-creator").exists()


def test_plugin_staging_rejects_existing_destination(tmp_path):
    checkout, _ = _plugin_checkout(tmp_path)
    (checkout / ".fullsend" / "plugins" / "epic-creator").mkdir(parents=True)
    assert _run_plugin_stager(checkout, tmp_path).returncode != 0


def test_plugin_staging_rejects_symlinked_destination_parent(tmp_path):
    checkout, _ = _plugin_checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (checkout / ".fullsend" / "plugins").symlink_to(outside, target_is_directory=True)
    assert _run_plugin_stager(checkout, tmp_path).returncode != 0
    assert list(outside.iterdir()) == []
