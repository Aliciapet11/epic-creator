# Local Epic Creator Fullsend package

This package runs the existing `epic-decompose` skill in a Fullsend Claude sandbox. It does not change CI or submit Jira work. The agent writes only `agent-result.json`; after successful validation, the trusted host post-script copies `artifacts/` and `tmp/` into an explicitly marked disposable checkout, replacing only those two directories.

## Setup

1. Configure `JIRA_SERVER`, `JIRA_USER`, `JIRA_TOKEN`, `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_APPLICATION_CREDENTIALS` in the local shell or an untracked env file outside the repository. Do not put real values in committed files. Of the Jira credentials, the runner receives only `JIRA_TOKEN`; the sandbox receives Jira server/user values and a provider-managed token placeholder. Jira egress is enforced read-only. The host credential file named by `GOOGLE_APPLICATION_CREDENTIALS` is mounted into the sandbox at `/tmp/.gcp-credentials.json` for Vertex inference; only use a trusted package and target checkout.
2. Stage the native Claude plugin before resolving or running the harness:

   ```bash
   bash .fullsend/prepare-plugin.sh
   ```

   It copies the current `.claude-plugin/`, `skills/`, `scripts/`, and `hooks/` trees into ignored `.fullsend/plugins/epic-creator/`. It refuses symlinks and an existing destination rather than overwriting it. To refresh, remove that generated destination explicitly, then stage again.
3. Resolve and pin the remote policy/provider/profile resources using the installed CLI:

   ```bash
   fullsend lock epic-creator --fullsend-dir .fullsend
   ```

   Once those resources are cached, `--offline` can be added to lock or run commands to prevent fetches.

## Synthetic fixture run (no live Jira)

Use a clean disposable checkout, not the source worktree; `.gitignore` is not an upload boundary. The example creates a detached checkout from explicit source revision `HEAD` and copies only the synthetic case-001 strategy and context into it. The marker is operator approval required before host bootstrap or post-run replacement of that checkout's `artifacts/` and `tmp/`:

```bash
TARGET="$(cd .. && pwd -P)/epic-creator-fullsend-disposable"
git worktree add --detach "$TARGET" HEAD
mkdir -p "$TARGET/artifacts/strat-tasks" "$TARGET/.context"
cp eval/cases/case-001-simple-two-component/artifacts/strat-tasks/RHAISTRAT-9001.md \
  "$TARGET/artifacts/strat-tasks/"
cp -R eval/cases/case-001-simple-two-component/.context/. \
  "$TARGET/.context/"
touch "$TARGET/.epic-fullsend-disposable"
```

From the package checkout, run with test-only placeholder Jira values and both existing bootstrap skip flags. These values are not credentials; the preseeded strategy/context mean the test should make no Jira or architecture-context calls:

```bash
JIRA_SERVER=https://example.invalid \
JIRA_USER=local-test@example.invalid \
JIRA_TOKEN=not-a-real-token \
EPIC_SKIP_BOOTSTRAP=1 \
EPIC_SKIP_COMPONENT_FETCH=1 \
FULLSEND_TASK='/epic-creator:epic-decompose RHAISTRAT-9001' \
fullsend run epic-creator --fullsend-dir .fullsend \
  --target-repo "$TARGET" --offline
```

This still uses real, billed Vertex inference. `--offline` prevents resource fetching; it does not disable model calls or container-image downloads.

Inspect Fullsend's validated `agent-result.json` and the disposable target's promoted `artifacts/` and `tmp/`. Validation requires pipeline phase `DONE`, a matching nonempty report, complete artifacts for each selected strategy, and matching review/report counts and statuses. A business review with `pass: false` remains a valid completed outcome. Validation fails closed if the final sweep has no repository snapshot; sweep-only recovery is not supported.

For JQL tasks, set trusted `FULLSEND_EXPECTED_STRATEGY_IDS` to the whitespace-separated strategy IDs expected from that query; validation fails closed if they are not supplied or do not match the packaged run. Do not pass Fullsend status-comment flags (`--status-comment-id`, `--status-number`, or `--status-repo`). The existing skill's `--announce-complete` option only prints completion status; it does not write Jira.

The first real inference run is intentionally separate from package setup and requires explicit approval. No CI change, Jira submission, or real workload is part of this local package.
