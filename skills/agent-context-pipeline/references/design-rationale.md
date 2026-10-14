# Design rationale

## Contents

- Trust model
- Evaluated pipeline
- Why the stack stays small
- Observed experiment
- Extension boundary

## Trust model

No single project document is permanently authoritative in a fast-changing
repository. The pipeline uses this precedence:

1. current source, working diff, and verification results;
2. refreshed structural graph;
3. fresh content-addressed checkpoints;
4. Git history and archaeology;
5. stale checkpoints, PRD, and architecture prose as historical intent.

A stale document is not discarded. It is labeled and used to explain why the
system evolved, never to override current code silently.

## Evaluated pipeline

```text
current code graph
    + Git commit and working-tree delta
    + product and architecture narrative
    + content-addressed checkpoints
    + verification evidence
    -> task-scoped brief + expandable evidence pack
```

The brief is the agent entry point. The evidence pack is a proof bundle, not a
document to load in full on every task.

Before an edit, the agent names the canonical contract, boundary, affected
files, consumers, and tests. After the edit, it reruns verification and the
compiler. At meaningful integration boundaries, it freezes a successor
checkpoint.

## Why the stack stays small

The evaluated critical path is Git + RepoWise + the local compiler in
`scripts/context_pipeline.py`.

- Git provides immutable commits, changed files, exact diffs, and archaeology.
- RepoWise provides current symbols, dependencies, smart skeletons, impact,
  co-change partners, hotspots, and health evidence.
- The local compiler adds stale/fresh classification, narrative evidence,
  working diffs, task scoping, and durable checkpoint semantics.

Coldstart-style content hashes and Selvedge-style decision fields were retained
as ideas. Additional memory engines were not placed on the critical path
because they duplicated evidence without improving the tested edit loop enough
to justify more state and failure modes.

## Observed experiment

The original repository experiment found an architecture document anchored to
an older commit. Since that anchor, 31 files had changed; 29 were
architecture-relevant code or configuration. The narrative was correctly
classified as stale.

A checkpoint was frozen with 31 SHA-256 anchors. It was initially fresh. After
one frontend renderer refactor, exactly that file became stale, and the
regenerated evidence pack included the exact uncommitted diff even though the
compact structural skeleton omitted the new constant body.

Observed timings on that repository:

- initial 228-file structural index: about 13 seconds;
- incremental refresh: about 6 seconds;
- brief and evidence compilation: about 3.5 seconds;
- post-edit feedback loop: about 10 seconds.

The implementation experiment passed web tests, repository typecheck, and the
compiler tests.

## Extension boundary

Add another source only when it contributes evidence not already available.
Normalize it into one of these categories:

- current structural truth;
- historical intent;
- verification evidence;
- freshness anchor;
- task scope or impact.

Do not let a new source bypass the trust gate. Keep `brief.md` bounded and put
large raw evidence in `evidence.md`. Add a deterministic test for every new
freshness, path-resolution, or filtering rule.
