# `epic-creator` Fullsend Agent

## Prerequisites

### Fullsend installed

See [Running agents locally](https://fullsend.sh/docs/guides/user/running-agents-locally) to set up a local Fullsend environment.

### jsonschema and pyyaml

The host scripts require `jsonschema` and `pyyaml`. You can provide them in a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install jsonschema pyyaml
```

### General CLIs

Install Git and curl for the bootstrap scripts.

### Environment variables

Create an environment file outside the repository and pass it to Fullsend with `--env-file`. Replace the example issue key with the strategy to decompose. Keep this file and the GCP JSON key out of version control.

```env
# Required inference variables
ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project>
GOOGLE_CLOUD_PROJECT=<your-gcp-project>
CLOUD_ML_REGION=global
GOOGLE_APPLICATION_CREDENTIALS=<absolute-path-to-gcp-json-key>

# Required variables
FULLSEND_TASK="/epic-decompose RHAISTRAT-1234"
JIRA_SERVER=https://redhat.atlassian.net
JIRA_USER="<email@example.com>"
JIRA_TOKEN="<token>"
```

Fullsend mounts the GCP key into the sandbox; use only a trusted package and target checkout.

## Run

Run from the repository root. Copyback replaces `artifacts/` and `tmp/` without rollback; use a checkout where those directories can be replaced.

```bash
fullsend run epic-creator --fullsend-dir .fullsend \
  --target-repo "$(pwd -P)" \
  --env-file /absolute/path/to/epic-creator.env
```

Skills are discovered from `.agents/skills/` in the target checkout. The current harness runs Claude Code, with compaction recovery provided by a project hook.

Results are copied into the target's `artifacts/` and `tmp/`. Metrics, transcripts, and logs are stored under `/tmp/fullsend/<sandbox>/`; keep them private. Runs use billed inference, including with `--offline`, which only prevents resource fetching.

Jira access is read-only. Submission is handled separately by host-side tools.
