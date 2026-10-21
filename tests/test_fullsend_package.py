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
RUN_START = "2026-10-20T00:00:00Z"
RUN_REPORT = Path(f"artifacts/decompose-runs/{RUN_START.replace(':', '-')}.yaml")


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
        yaml.safe_dump({"phase": phase, "start_time": RUN_START}), encoding="utf-8"
    )
    runs = repo / "artifacts" / "decompose-runs"
    runs.mkdir(parents=True)
    if report:
        (repo / RUN_REPORT).write_text(
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
    assert [agent for agent in config["agents"] if "source" in agent] == [
        {"source": "epic-creator/epic-creator.yaml"}
    ]
    harness = yaml.safe_load(HARNESS.read_text(encoding="utf-8"))
    assert harness["role"] == "epic-creator"
    assert harness["slug"] == "fullsend-ai-epic-creator"
    assert "plugins" not in harness
    assert not (FULLSEND / "prepare-plugin.sh").exists()
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
    for key in ("EPIC_SKIP_BOOTSTRAP", "EPIC_SKIP_COMPONENT_FETCH"):
        assert harness["env"]["sandbox"][key] == "1"

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


def test_package_documentation_layout():
    landing = (FULLSEND / "README.md").read_text(encoding="utf-8")
    assert "[agent README](epic-creator/README.md)" in landing
    assert (FULLSEND / "epic-creator" / "README.md").is_file()


def test_native_skill_prompt_contract():
    prompt = (FULLSEND / "epic-creator" / "prompt.md").read_text(encoding="utf-8")
    for phrase in (
        "call the `Skill` tool for `epic-decompose`",
        "only if the tool actually reports the skill unavailable",
        "resume the existing dispatcher in place",
        "without calling the `Skill` tool",
        "If the phase is `INIT`",
        "without running `init` or overwriting existing ID or batch files",
        "Otherwise skip setup, bootstrap, and rebatching",
        "Start with `python3 scripts/pipeline_state.py next-action`",
        "its explicit `subagent_type` if present, otherwise `general-purpose`",
        "never use the `epic-creator` Fullsend wrapper as a phase worker",
        "write only `FULLSEND_OUTPUT_DIR/agent-result.json`",
        "Do not copy `artifacts/` or `tmp/`",
    ):
        assert phrase in prompt
    assert prompt.index("resume the existing dispatcher") < prompt.index("for a new run")


def test_validator_accepts_completed_run_with_failed_business_review(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    result = _run_validator(iteration, repo)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "PASS: pipeline phase DONE" in result.stdout
    assert sorted(path.name for path in output.iterdir()) == ["agent-result.json"]


@pytest.mark.parametrize("case", ["older-report", "dotfile", "empty-current-report"])
def test_validator_requires_nonempty_current_run_report(tmp_path, case):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration, report=False)
    runs = repo / "artifacts/decompose-runs"
    if case == "older-report":
        (runs / "2026-10-19T00-00-00Z.yaml").write_text("previous run\n")
    elif case == "dotfile":
        (runs / ".placeholder").write_text("not a report\n")
    else:
        (repo / RUN_REPORT).touch()
    result = _run_validator(iteration, repo)
    assert result.returncode != 0
    assert "current run report" in result.stdout


@pytest.mark.parametrize("start_time", [
    None, "", "not-a-timestamp", "../../outside", 42,
    "2026-02-30T00:00:00Z", "2026-10-20T00:00:00+00:00",
    "0000-01-01T00:00:00Z", "2100-02-29T00:00:00Z",
    "2026-04-31T00:00:00Z", "2026-10-20T24:00:00Z",
    "2026-10-20T00:60:00Z", "2026-10-20T00:00:60Z",
])
def test_validator_rejects_missing_or_invalid_run_start(tmp_path, start_time):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration)
    state = {"phase": "DONE"}
    if start_time is not None:
        state["start_time"] = start_time
    (repo / "tmp/pipeline-state.yaml").write_text(yaml.safe_dump(state))
    result = _run_validator(iteration, repo)
    assert result.returncode != 0
    assert "start_time" in result.stdout


@pytest.mark.parametrize("start_time", ["2000-02-29T00:00:00Z", "2024-02-29T23:59:59Z"])
@pytest.mark.parametrize("quote", ["'", '"', ""])
def test_validator_accepts_utc_report_timestamp(tmp_path, start_time, quote):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration)
    (repo / "tmp/pipeline-state.yaml").write_text(
        f"phase: DONE\nstart_time: {quote}{start_time}{quote}\n"
    )
    (repo / RUN_REPORT).rename(
        repo / "artifacts/decompose-runs" / f"{start_time.replace(':', '-')}.yaml"
    )
    result = _run_validator(iteration, repo)
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("value", [f"'{RUN_START}", f'{RUN_START}"', f"'{RUN_START}\""])
def test_validator_rejects_unpaired_timestamp_quotes(tmp_path, value):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration)
    (repo / "tmp/pipeline-state.yaml").write_text(f"phase: DONE\nstart_time: {value}\n")
    assert _run_validator(iteration, repo).returncode != 0


def test_validator_rejects_duplicate_run_start(tmp_path):
    iteration = tmp_path / "iteration-1"
    _, repo = _fixture(iteration)
    (repo / "tmp/pipeline-state.yaml").write_text(
        f"phase: DONE\nstart_time: '{RUN_START}'\nstart_time: '{RUN_START}'\n"
    )
    assert _run_validator(iteration, repo).returncode != 0


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
    report = RUN_REPORT
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
    assert (repo / RUN_REPORT).is_file()


def test_post_removal_failure_stops_copyback(tmp_path):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    target = _disposable_target(tmp_path / "target")
    result = subprocess.run(
        ["bash", "-c", 'rm() { return 1; }; source "$1"', "_", str(POST)],
        cwd=iteration,
        env=_environment(
            tmp_path, FULLSEND_VALIDATED_ITERATION_DIR=output,
            REPO_DIR=repo, TARGET_REPO_DIR=target,
        ),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert (target / "tmp/old.txt").read_text() == "replace\n"
    assert (target / "artifacts/old.txt").read_text() == "replace\n"
    assert not (target / RUN_REPORT).exists()


@pytest.mark.parametrize("variable", [
    "TARGET_REPO_DIR", "FULLSEND_VALIDATED_ITERATION_DIR", "REPO_DIR",
])
@pytest.mark.parametrize("empty", [False, True])
def test_post_rejects_missing_or_empty_required_paths(tmp_path, variable, empty):
    iteration = tmp_path / "iteration-1"
    output, repo = _fixture(iteration)
    target = _disposable_target(tmp_path / "target")
    env = _environment(
        tmp_path, FULLSEND_VALIDATED_ITERATION_DIR=output,
        REPO_DIR=repo, TARGET_REPO_DIR=target,
    )
    if empty:
        env[variable] = ""
    else:
        env.pop(variable)
    result = subprocess.run(
        ["bash", "-c", 'rm() { echo DELETE_CALLED >&2; return 99; }; source "$1"',
         "_", str(POST)],
        cwd=iteration, env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert f"{variable} is required" in result.stderr
    assert "DELETE_CALLED" not in result.stderr
    assert (target / "tmp/old.txt").is_file()
    assert (target / "artifacts/old.txt").is_file()


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


def test_repository_skill_layout():
    repo = ROOT.resolve()
    assert not (ROOT / "skills").is_symlink()
    skill_root = (ROOT / ".agents/skills").resolve()
    assert repo in skill_root.parents
    assert (ROOT / "skills").resolve() == skill_root
    assert (ROOT / ".claude/skills").resolve() == skill_root

    skill = skill_root / "epic-decompose"
    compatibility = ROOT / "skills/epic-decompose"
    for relative in (
        "SKILL.md",
        "prompts/fetch-agent.md",
        "prompts/decompose-agent.md",
        "prompts/review-agent.md",
        "prompts/revise-agent.md",
    ):
        assert (skill / relative).is_file()
        assert (compatibility / relative).read_bytes() == (skill / relative).read_bytes()


def _compact_hook_commands(path):
    config = json.loads(path.read_text())
    entries = config["hooks"]["SessionStart"]
    matches = [entry for entry in entries if entry.get("matcher") == "compact"]
    assert len(matches) == 1
    return [
        hook["command"]
        for hook in matches[0]["hooks"]
        if hook.get("type") == "command"
    ]


def test_plugin_manifest_and_compact_hooks():
    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "epic-creator"
    assert "skills" not in manifest

    plugin_commands = _compact_hook_commands(ROOT / "hooks/hooks.json")
    assert plugin_commands == [
        "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/pipeline_state.py post-compact-hook"
    ]

    project_commands = _compact_hook_commands(ROOT / ".claude/settings.json")
    assert len(project_commands) == 1
    command = project_commands[0]
    assert 'if [ "${FULLSEND_ROLE:-}" = "epic-creator" ]' in command
    assert 'cd "${CLAUDE_PROJECT_DIR:?}"' in command
    assert "python3 scripts/pipeline_state.py post-compact-hook" in command


def _hook_checkout(tmp_path, phase="DECOMPOSE"):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/pipeline_state.py", scripts / "pipeline_state.py")
    if phase is not None:
        state = checkout / "tmp/pipeline-state.yaml"
        state.parent.mkdir()
        state.write_text(f"phase: {phase}\n")
    return checkout


def _run_hook(command, cwd, tmp_path, **extra_env):
    return subprocess.run(
        ["bash", "-c", command],
        cwd=cwd,
        env=_environment(tmp_path, **extra_env),
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("phase", ["DECOMPOSE", "INIT"])
def test_dispatch_resume_preserves_run_and_existing_batches(tmp_path, phase):
    checkout = _hook_checkout(tmp_path)
    for name in ("check_decompose_progress.py", "artifact_utils.py"):
        shutil.copy2(ROOT / "scripts" / name, checkout / "scripts" / name)
    state_path = checkout / "tmp/pipeline-state.yaml"
    state = {
        "phase": phase, "start_time": RUN_START, "batch_size": 25,
        "batch": 0 if phase == "INIT" else 1,
        "total_batches": 0 if phase == "INIT" else 1, "headless": True,
    }
    state_path.write_text(yaml.safe_dump(state))
    ids = "RHAISTRAT-1234\n"
    paths = [checkout / "tmp" / name for name in (
        "pipeline-all-ids.txt", "pipeline-batch-1-ids.txt", "pipeline-active-ids.txt",
    )]
    for path in paths:
        path.write_text(ids)
    commands = []
    if phase == "INIT":
        commands.extend([["set", "total_batches=1"], ["set-phase", "BATCH_START"]])
    commands.append(["next-action"])
    for arguments in commands:
        result = subprocess.run(
            ["python3", "scripts/pipeline_state.py", *arguments], cwd=checkout,
            env=_environment(tmp_path), capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr + result.stdout
    action = yaml.safe_load(result.stdout)
    assert action["action"] == "launch_wave"
    assert action["phase"] == ("FETCH" if phase == "INIT" else phase)
    after = yaml.safe_load(state_path.read_text())
    for key in ("start_time", "batch_size", "headless"):
        assert after[key] == state[key]
    for path in paths:
        assert path.read_text() == ids


@pytest.mark.parametrize(
    ("phase", "enabled", "nested", "expected"),
    [
        ("DECOMPOSE", "1", False, "[PIPELINE STATE RECOVERY] Current phase: DECOMPOSE"),
        ("DECOMPOSE", "1", True, "[PIPELINE STATE RECOVERY] Current phase: DECOMPOSE"),
        ("DONE", "1", False, "[PIPELINE STATE RECOVERY] Pipeline complete"),
        ("DECOMPOSE", "", False, ""),
        (None, "1", False, ""),
    ],
)
def test_project_compact_hook_recovery(tmp_path, phase, enabled, nested, expected):
    checkout = _hook_checkout(tmp_path, phase)
    cwd = checkout / "nested" if nested else checkout
    if nested:
        cwd.mkdir()
    command = _compact_hook_commands(ROOT / ".claude/settings.json")[0]
    result = _run_hook(
        command,
        cwd,
        tmp_path,
        EPIC_CREATOR_ENABLE_CONTEXT_HOOK=enabled,
        FULLSEND_ROLE="epic-creator",
        CLAUDE_PROJECT_DIR=str(checkout),
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if expected:
        assert expected in result.stdout
    else:
        assert result.stdout == ""


def test_project_hook_does_not_duplicate_plugin_hook(tmp_path):
    checkout = _hook_checkout(tmp_path)
    project_command = _compact_hook_commands(ROOT / ".claude/settings.json")[0]
    plugin_command = _compact_hook_commands(ROOT / "hooks/hooks.json")[0]
    env = {
        "EPIC_CREATOR_ENABLE_CONTEXT_HOOK": "1",
        "CLAUDE_PROJECT_DIR": str(checkout),
        "CLAUDE_PLUGIN_ROOT": str(checkout),
    }

    project_result = _run_hook(project_command, checkout, tmp_path, **env)
    assert project_result.returncode == 0, project_result.stderr
    assert project_result.stderr == ""
    assert project_result.stdout == ""

    plugin_result = _run_hook(plugin_command, checkout, tmp_path, **env)
    assert plugin_result.returncode == 0, plugin_result.stderr
    assert plugin_result.stderr == ""
    assert plugin_result.stdout.count(
        "[PIPELINE STATE RECOVERY] Current phase: DECOMPOSE"
    ) == 1
