# Operations

## Contents

- Installation
- Repository bootstrap
- Artifact layout
- Dependency behavior
- Troubleshooting

## Installation

Keep the directory intact: `SKILL.md`, `agents/`, `scripts/`, and `references/`
are one unit.

On this machine the skill is installed once, globally:

- canonical copy: `~/.agents/skills/agent-context-pipeline/` (Codex reads it
  from here);
- Claude Code: symlink `~/.claude/skills/agent-context-pipeline ->
  ../../.agents/skills/agent-context-pipeline`.

Do not copy the skill into repositories. A repository needs only what the
`create-repo` skill adds: `.repowise/` and `.context/packs/` in `.gitignore`,
an empty `.context/checkpoints/`, and a short pointer section in `AGENTS.md`.
Old per-repository copies (`.agents/skills/agent-context-pipeline/`) shadow
the global one; remove them when the project is next touched.

For ordinary Claude.ai chat, package the complete skill directory as a ZIP and
upload it through **Customize → Skills → Create skill → Upload a skill**. This
requires code execution/file creation to be enabled.

Required runtime dependencies:

- Git;
- Python 3.10 or newer;
- RepoWise available on `PATH`, via `REPOWISE_BIN`, or through
  `--repowise /absolute/path/to/repowise`.

The package was verified with RepoWise `0.36.0`. For a reproducible isolated
installation:

```bash
uv tool install "repowise==0.36.0"
```

The compiler needs the Python `mcp` package. It normally re-executes itself with
the Python interpreter belonging to RepoWise, where that dependency is already
installed. If this cannot be discovered, run the compiler with RepoWise's
interpreter or install `mcp` in the active Python environment.

## Repository bootstrap

Use `scripts/run_pipeline.py`. If `.repowise/state.json` is absent, it runs an
index-only RepoWise initialization without LLM prose generation. Otherwise it
runs an incremental index-only update.

The runner always disables RepoWise telemetry. It snapshots these files before
RepoWise setup and restores them afterward:

- `.mcp.json`;
- `.vscode/mcp.json`;
- `.vscode/extensions.json`.

`.repowise/` itself remains in the repository working tree because it is the
structural index. It is ignored by git (`create-repo` adds the line); the first
pipeline run in a fresh clone rebuilds it.

## Self-test

After copying the folder, run:

```bash
python3 -m unittest discover \
  -s "$SKILL_DIR/scripts" \
  -p "test_*.py" \
  -v
```

This test does not require RepoWise. A full smoke test is the normal
`scripts/run_pipeline.py` invocation against a disposable clone.

## Artifact layout

The default runner output is:

```text
.context/
  packs/
    YYYYMMDDTHHMMSSZ/
      brief.md
      evidence.md
  checkpoints/
    accepted-checkpoint.md
```

`brief.md` is the entry surface for the coding agent. `evidence.md` is the
expandable proof bundle. Checkpoints are durable, content-addressed project
history.

## Dependency behavior

The compiler does not require an LLM provider for RepoWise. Without one,
RepoWise's `get_answer` section is explicitly degraded, while symbol search,
smart skeletons, impact analysis, Git archaeology, health analysis, and
checkpoint freshness remain available.

The coding agent performs the final synthesis from the deterministic brief and
evidence. Configuring a RepoWise LLM provider is optional.

## Troubleshooting

- `RepoWise executable not found`: install it or pass `--repowise`.
- `Python MCP package is unavailable`: run the script using the Python
  interpreter whose environment contains RepoWise.
- Empty or stale structural results: run the runner again; it refreshes the
  index before compiling context.
- RepoWise editor configuration changed: report this as a runner defect. The
  wrapper must restore the three protected files exactly.
- A checkpoint is `STALE`: do not repair the old file. Verify the changed code
  and create a successor checkpoint.
