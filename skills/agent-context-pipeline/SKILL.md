---
name: agent-context-pipeline
description: Build task-scoped coding context from the current RepoWise code graph, Git history and diffs, narrative architecture/PRD files, and content-addressed checkpoints. Use before architecture-sensitive implementation, frontend-backend integration, large refactors, resuming fast-changing or vibe-coded projects, impact analysis, or freezing a reliable context checkpoint when product requirements and architecture may have drifted.
---

# Agent Context Pipeline

Compile current structural truth and historical intent into a bounded editing
brief so the coding agent does not rediscover the repository from zero.

Treat source code and the refreshed code graph as current structural truth.
Treat PRD, architecture documents, and old checkpoints as explanatory evidence
whose anchors may be stale.

## Resolve the environment

1. Resolve the repository root with `git rev-parse --show-toplevel`.
2. Resolve `SKILL_DIR` as the directory containing this `SKILL.md`.
3. Require Python 3.10+, Git, and RepoWise.
4. If RepoWise is missing, stop and ask before installing it. Prefer an isolated
   installation such as `uv tool install repowise`.
5. Disable RepoWise telemetry in every invocation with both `DO_NOT_TRACK=1`
   and `REPOWISE_TELEMETRY_DISABLED=1`.

Read [references/operations.md](references/operations.md) when installing,
bootstrapping a new repository, selecting output locations, or troubleshooting.
Read [references/design-rationale.md](references/design-rationale.md) when
changing the pipeline, evaluating another memory tool, or explaining the trust
model to a team.

## Build context before editing

Turn the user request into one precise task statement. Add two to six seed
`--focus` files when the likely boundary is known; omit them when discovery is
the goal.

Run:

```bash
python3 "$SKILL_DIR/scripts/run_pipeline.py" \
  --repo "$REPO_ROOT" \
  --task "THE PRECISE TASK" \
  --focus path/to/primary-file \
  --focus path/to/shared-contract
```

Add each relevant prior checkpoint:

```bash
  --checkpoint .context/checkpoints/checkpoint-name.md
```

The runner:

1. creates or incrementally refreshes a structural RepoWise index;
2. protects pre-existing `.mcp.json` and `.vscode` configuration from RepoWise
   setup side effects;
3. writes a compact `brief.md`;
4. writes a detailed `evidence.md`.

Read `brief.md` first. In `evidence.md`, read only:

- task-scoped code context;
- why and historical rationale;
- change impact and affected surface;
- current diff and freshness sections.

Expand omitted symbols only when the planned edit requires their bodies.

## Establish the edit contract

Before changing code, state:

- canonical product/backend contract;
- frontend, adapter, or projection boundary;
- files expected to change;
- transitive consumers and co-change partners;
- tests expected to prove the change;
- stale documents or checkpoints that must not be trusted as current truth.

Do not create a parallel frontend/domain model when the repository already has a
canonical backend contract. Do not update a narrative document merely to make a
freshness warning disappear; reconcile it with current code and evidence.

## Implement and verify

Implement through the discovered contract boundary.

Run targeted tests first, then the repository's normal typecheck and broader
suite in proportion to risk. Pay particular attention to:

- files marked as hotspots or bug magnets;
- test gaps;
- co-change partners;
- cross-layer contracts;
- current uncommitted diff, which may contain evidence omitted by a compact code
  skeleton.

After editing, rerun the same pipeline command. Confirm that:

- the graph sees the current files;
- the current diff is present;
- changed checkpoint anchors are explicitly `STALE`;
- the editing slice still covers the actual change;
- no unexpected consumers or tests appeared.

## Freeze a checkpoint

Create a checkpoint at a meaningful integration boundary, not after every tiny
commit. Read [references/checkpoint-schema.md](references/checkpoint-schema.md)
before the first checkpoint in a repository.

Run:

```bash
python3 "$SKILL_DIR/scripts/context_pipeline.py" checkpoint \
  --repo "$REPO_ROOT" \
  --since BASELINE_COMMIT \
  --title "Checkpoint title" \
  --intent "Original product intent" \
  --outcome "What now actually works" \
  --architecture-delta "How boundaries or contracts changed" \
  --decision "Accepted invariant or decision" \
  --rejected "Rejected or failed approach and why" \
  --test "Verification command and result" \
  --open-question "Remaining uncertainty or risk" \
  --output .context/checkpoints/checkpoint-name.md
```

Repeat `--decision`, `--rejected`, `--test`, and `--open-question` as needed.
Commit checkpoints that represent accepted project history. Keep task packs
generated unless the team deliberately wants to preserve them.

Never rewrite an old checkpoint to match new code. Let its file hashes become
stale, preserve it as history, and create a successor checkpoint.

## Completion gate

Finish only when:

- the agent brief is grounded in the refreshed graph;
- the evidence pack contains exact current diff evidence;
- stale narrative claims are labeled;
- implementation verification is recorded;
- a requested checkpoint contains product intent, actual outcome, architecture
  delta, decisions, rejected attempts, risks, commits, changed files, and file
  hashes.
