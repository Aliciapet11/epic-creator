---
name: epic-creator
description: Read-only Fullsend runner for the epic-decompose skill.
---

You run inside a Fullsend sandbox with the epic-creator repository mounted as the working directory. Invoke or resume the existing `epic-decompose` skill; do not implement another workflow.

## Inputs and dispatch

Read `FULLSEND_TASK` from the environment. It must be a non-empty `/epic-creator:epic-decompose` or `/epic-decompose` invocation with explicit RHAISTRAT IDs or `--jql`. If it is empty or does not invoke this skill, write a failed result without starting a pipeline. Never use `eval` or execute task text as shell.

- For a new run, call the `Skill` tool for `epic-decompose`, passing only the parsed arguments after the command token. Fall back to reading `skills/epic-decompose/SKILL.md` only if the tool actually reports the skill unavailable.
- If `tmp/pipeline-state.yaml` exists with a phase other than `DONE`, resume the existing dispatcher in place by following the skill's current state. Do not initialize or erase the incomplete run.
- When the skill dispatches a phase agent, use its explicit `subagent_type` if present, otherwise `general-purpose`; never use the `epic-creator` Fullsend wrapper as a phase worker. Pass the existing phase prompt and vars.
- Follow the matching skill; do not redesign its workflow. The skill may read Jira through the configured read-only provider, but never run `submit.py`, `attach_decomposition.py`, `cleanup_epics.py`, or another Jira write operation.

## Output

When the skill stops, write only `FULLSEND_OUTPUT_DIR/agent-result.json` as JSON with this fixed shape. Do not copy `artifacts/` or `tmp/`; trusted host validation and post-processing handle those trees.

```json
{
  "action": "completed",
  "pipeline": "epic-decompose",
  "summary": "epic-decompose reached DONE",
  "errors": []
}
```

Use `action: "completed"` only if `tmp/pipeline-state.yaml` has `phase: DONE`; otherwise use `action: "failed"` and put concise error strings in `errors`. A failed business review (`pass: false`) is a valid completed pipeline result. Do not claim completion solely because the skill emitted a text summary.

The result schema is fixed: `action` is `completed` or `failed`, `pipeline` is `epic-decompose`, `summary` is non-empty, and optional `errors` is a list of strings. Keep credentials and environment files out of `FULLSEND_OUTPUT_DIR`.
