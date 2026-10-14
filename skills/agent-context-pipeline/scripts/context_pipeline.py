#!/usr/bin/env python3
"""Compile current code, history, docs, and decisions into agent context."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_REPOWISE = os.environ.get("REPOWISE_BIN") or shutil.which("repowise") or "repowise"
PATH_PATTERN = re.compile(
    r"(?<![\w./-])([A-Za-z0-9_@().+-]+"
    r"(?:/[A-Za-z0-9_@().+:-]+)+)"
)
DECLARED_CODE_PATTERN = re.compile(
    r"(?:код|code|commit)[^`\n]{0,30}`([0-9a-f]{7,40})`",
    re.IGNORECASE,
)
WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_-]{3,}")
STOP_WORDS = {
    "this",
    "that",
    "with",
    "from",
    "into",
    "котор",
    "чтобы",
    "этого",
    "когда",
    "надо",
    "хотим",
    "сейчас",
    "будет",
    "через",
    "frontend",
    "backend",
}
TOOL_ARTIFACT_PREFIXES = (
    ".repowise/",
    ".vscode/",
    ".coldstart/",
    ".selvedge/",
)
TOOL_ARTIFACT_FILES = {".mcp.json"}


@dataclass(frozen=True)
class GitState:
    head: str
    branch: str
    status: str
    declared_architecture_ref: str | None
    declared_architecture_commit: str | None
    architecture_ref_valid: bool
    changed_since_architecture: tuple[str, ...]
    architecture_relevant_changed: tuple[str, ...]


def run(
    argv: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> str:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        text=True,
        encoding="utf-8",
        # `git diff --stat` truncates long non-ASCII paths mid-character.
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed:\n{proc.stdout}")
    return proc.stdout.strip()


def succeeds(argv: list[str], *, cwd: Path) -> bool:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def resolve_repowise(value: str) -> str:
    discovered = shutil.which(value)
    if discovered:
        return discovered
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    raise RuntimeError(
        "RepoWise executable not found. Install RepoWise or pass "
        "--repowise /absolute/path/to/repowise."
    )


def ensure_mcp_runtime(repowise: str) -> None:
    try:
        import mcp  # noqa: F401

        return
    except ImportError:
        pass

    if os.environ.get("AGENT_CONTEXT_PIPELINE_REEXEC") == "1":
        raise RuntimeError(
            "The Python MCP package is unavailable. Run this script with the "
            "Python interpreter that owns the RepoWise installation."
        )

    executable = Path(repowise)
    candidates: list[Path] = []
    try:
        first_line = executable.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()[0]
        if first_line.startswith("#!"):
            shebang = shlex.split(first_line[2:].strip())
            if shebang and Path(shebang[0]).is_file():
                candidates.append(Path(shebang[0]))
    except (OSError, IndexError):
        pass
    candidates.extend(
        [
            executable.parent / "python",
            executable.parent / "python3",
        ]
    )

    for interpreter in candidates:
        if not interpreter.is_file() or interpreter.resolve() == Path(sys.executable).resolve():
            continue
        probe = subprocess.run(
            [str(interpreter), "-c", "import mcp"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            environment = dict(os.environ)
            environment["AGENT_CONTEXT_PIPELINE_REEXEC"] = "1"
            os.execve(
                str(interpreter),
                [
                    str(interpreter),
                    str(Path(__file__).resolve()),
                    *sys.argv[1:],
                ],
                environment,
            )

    raise RuntimeError(
        "The Python MCP package is unavailable. Run the script with RepoWise's "
        "Python interpreter, or install the `mcp` package in the active environment."
    )


def git(repo: Path, *args: str, check: bool = True) -> str:
    # Without this git C-quotes non-ASCII paths ("\320..."), which breaks suffix checks
    # such as `.endswith(".md")` for Cyrillic file names.
    return run(["git", "-c", "core.quotepath=false", *args], cwd=repo, check=check)


def read_git_state(repo: Path) -> GitState:
    head = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "branch", "--show-current") or "(detached)"
    status = git(repo, "status", "--short")
    architecture = repo / "ARCHITECTURE.md"
    declared = None
    if architecture.exists():
        match = DECLARED_CODE_PATTERN.search(
            architecture.read_text(encoding="utf-8", errors="replace")[:4000]
        )
        declared = match.group(1) if match else None
    valid = False
    declared_commit = None
    changed: tuple[str, ...] = ()
    if declared:
        valid = succeeds(
            ["git", "cat-file", "-e", f"{declared}^{{commit}}"],
            cwd=repo,
        )
        if valid:
            declared_commit = git(repo, "rev-parse", declared)
            changed_text = git(repo, "diff", "--name-only", f"{declared}..HEAD")
            changed = tuple(line for line in changed_text.splitlines() if line)
    return GitState(
        head=head,
        branch=branch,
        status=status,
        declared_architecture_ref=declared,
        declared_architecture_commit=declared_commit,
        architecture_ref_valid=valid,
        changed_since_architecture=changed,
        architecture_relevant_changed=tuple(
            path for path in changed if is_architecture_relevant(path)
        ),
    )


def sha256(repo: Path, relative: str) -> str:
    path = repo / relative
    if not path.is_file():
        return "missing"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def is_tool_artifact(relative: str) -> bool:
    return relative in TOOL_ARTIFACT_FILES or relative.startswith(
        TOOL_ARTIFACT_PREFIXES
    )


def is_architecture_relevant(relative: str) -> bool:
    if is_tool_artifact(relative) or relative.endswith(".md"):
        return False
    return not relative.startswith(("docs/", "skills/"))


def architecture_is_fresh(state: GitState) -> bool:
    return bool(
        state.declared_architecture_ref
        and state.architecture_ref_valid
        and not state.architecture_relevant_changed
    )


def filtered_status(status: str) -> str:
    kept = []
    for line in status.splitlines():
        relative = line[3:] if len(line) > 3 else ""
        if relative and not is_tool_artifact(relative):
            kept.append(line)
    return "\n".join(kept)


def trim(text: str, max_chars: int = 45_000) -> str:
    if len(text) <= max_chars:
        return text
    tail = "\n\n[section truncated by context compiler]"
    return text[: max_chars - len(tail)] + tail


def mcp_text(result: object) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []):
        value = getattr(item, "text", None)
        if value:
            parts.append(value)
    return "\n".join(parts).strip()


def parse_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def normalize_repo_file(repo: Path, candidate: object) -> str | None:
    if not isinstance(candidate, (str, os.PathLike)):
        return None
    raw = str(candidate).strip().rstrip(".,;:)]}`'")
    if not raw:
        return None
    path = Path(raw)
    resolved = path.resolve() if path.is_absolute() else (repo / path).resolve()
    try:
        relative = resolved.relative_to(repo.resolve())
    except ValueError:
        return None
    if not resolved.is_file():
        return None
    return relative.as_posix()


def resolve_repo_path(repo: Path, path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (repo / expanded).resolve()


def discover_repo_files(repo: Path, search: str) -> list[str]:
    discovered: list[str] = []

    def add(candidate: object) -> None:
        relative = normalize_repo_file(repo, candidate)
        if relative and relative not in discovered:
            discovered.append(relative)

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key in ("file", "file_path", "path", "target_path"):
                add(value.get(key))
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    parsed = parse_json(search)
    if parsed is not None:
        walk(parsed)
    for candidate in PATH_PATTERN.findall(search):
        add(candidate)
    return discovered


def compact_overview(text: str) -> str:
    value = parse_json(text)
    if not isinstance(value, dict):
        return trim(text, 25_000)
    selected = {
        key: value[key]
        for key in (
            "title",
            "content_md",
            "code_health",
            "key_modules",
            "entry_points",
            "architecture",
            "reading_order",
            "guided_tour",
            "_meta",
        )
        if key in value
    }
    return json.dumps(selected, ensure_ascii=False, indent=2)


def compact_why(text: str) -> str:
    value = parse_json(text)
    if not isinstance(value, dict):
        return trim(text, 20_000)
    targets: dict[str, object] = {}
    for path, raw in value.get("target_context", {}).items():
        if not isinstance(raw, dict):
            continue
        origin = raw.get("origin", {})
        archaeology = raw.get("git_archaeology", {})
        targets[path] = {
            "governing_decisions": raw.get("governing_decisions", []),
            "origin_summary": origin.get("summary") if isinstance(origin, dict) else None,
            "key_commits": (
                origin.get("key_commits", [])[:8] if isinstance(origin, dict) else []
            ),
            "git_archaeology_summary": (
                archaeology.get("summary") if isinstance(archaeology, dict) else None
            ),
            "direct_commits": (
                archaeology.get("file_commits", [])[:8]
                if isinstance(archaeology, dict)
                else []
            ),
            "cross_references": (
                archaeology.get("cross_references", [])[:6]
                if isinstance(archaeology, dict)
                else []
            ),
        }
    selected = {
        "query": value.get("query"),
        "decisions": value.get("decisions", []),
        "targets": targets,
        "code_rationale": value.get("code_rationale", []),
        "_meta": value.get("_meta", {}),
    }
    return json.dumps(selected, ensure_ascii=False, indent=2)


def compact_risk(text: str) -> str:
    value = parse_json(text)
    if not isinstance(value, dict):
        return trim(text, 25_000)
    targets: dict[str, object] = {}
    keep = (
        "risk_summary",
        "hotspot_score",
        "dependents_count",
        "risk_type",
        "change_pattern",
        "co_change_partners",
        "impact_surface",
        "test_gap",
        "defect_profile",
        "health_score",
        "top_biomarkers",
        "security_signals",
    )
    for path, raw in value.get("targets", {}).items():
        if isinstance(raw, dict):
            targets[path] = {key: raw[key] for key in keep if key in raw}
    selected = {
        "targets": targets,
        "global_hotspots": value.get("global_hotspots", [])[:8],
        "_meta": value.get("_meta", {}),
    }
    return json.dumps(selected, ensure_ascii=False, indent=2)


async def collect_repowise(
    *,
    repo: Path,
    repowise: str,
    task: str,
    explicit_focus: list[str],
) -> dict[str, str | list[str]]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(
        command=repowise,
        args=["mcp", str(repo), "--transport", "stdio"],
        env={
            **os.environ,
            "DO_NOT_TRACK": "1",
            "REPOWISE_TELEMETRY_DISABLED": "1",
        },
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            overview = mcp_text(await session.call_tool("get_overview", {}))
            search = mcp_text(
                await session.call_tool(
                    "search_codebase",
                    {"query": task, "limit": 15, "mode": "auto"},
                )
            )
            answer = mcp_text(
                await session.call_tool(
                    "get_answer",
                    {
                        "question": (
                            f"{task}\n\nExplain the current architecture slice, the "
                            "backend/frontend contract, the safest implementation "
                            "boundary, affected tests, and the main risks. Ground "
                            "every claim in current repository evidence."
                        )
                    },
                )
            )

            discovered = discover_repo_files(repo, search)
            focus: list[str] = []
            for candidate in [*explicit_focus, *discovered]:
                relative = normalize_repo_file(repo, candidate)
                if relative and relative not in focus:
                    focus.append(relative)
            focus = focus[:12]
            context_focus = []
            for candidate in explicit_focus or focus:
                if candidate in focus and candidate not in context_focus:
                    context_focus.append(candidate)
            context_focus = context_focus[:6]

            if context_focus:
                context = mcp_text(
                    await session.call_tool(
                        "get_context",
                        {"targets": context_focus, "compact": True},
                    )
                )
                why = mcp_text(
                    await session.call_tool(
                        "get_why",
                        {"query": task, "targets": context_focus[:4]},
                    )
                )
                risk = mcp_text(
                    await session.call_tool(
                        "get_risk",
                        {"targets": context_focus[:4]},
                    )
                )
                health = mcp_text(
                    await session.call_tool(
                        "get_health",
                        {"targets": context_focus, "limit": 20},
                    )
                )
            else:
                context = "No focus files were resolved from structural search."
                why = "No focus files were available for rationale lookup."
                risk = "No focus files were available for impact analysis."
                health = "No focus files were available for health analysis."

            return {
                "overview": compact_overview(overview),
                "search": trim(search),
                "answer": trim(answer, 30_000),
                "focus": focus,
                "context_focus": context_focus,
                "context": trim(context),
                "why": compact_why(why),
                "risk": compact_risk(risk),
                "health": trim(health, 15_000),
            }


def relevant_doc_evidence(repo: Path, task: str, limit: int = 100) -> str:
    terms = {
        word.casefold()
        for word in WORD_PATTERN.findall(task)
        if word.casefold() not in STOP_WORDS
    }
    if not terms:
        return "No useful task terms were available for document matching."
    roots = [
        repo / "ARCHITECTURE.md",
        repo / "EXPERIMENT.md",
        repo / "docs" / "architecture",
    ]
    candidates: list[Path] = []
    for root in roots:
        if root.is_file():
            candidates.append(root)
        elif root.is_dir():
            candidates.extend(sorted(root.rglob("*.md")))
    matches: list[str] = []
    for path in candidates:
        relative = path.relative_to(repo)
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(),
            start=1,
        ):
            folded = line.casefold()
            score = sum(1 for term in terms if term in folded)
            if score >= 2:
                matches.append(f"{relative}:{line_number} [{score}] {line.strip()}")
    matches.sort(key=lambda item: (-int(item.split("[", 1)[1].split("]", 1)[0]), item))
    if not matches:
        return "No matching architectural document lines were found."
    return "\n".join(matches[:limit])


def checkpoint_freshness(repo: Path, checkpoints: Iterable[Path]) -> str:
    entries: list[str] = []
    anchor_pattern = re.compile(
        r"^- `([^`]+)` — `(sha256:[0-9a-f]{64}|missing)`$", re.MULTILINE
    )
    for checkpoint in checkpoints:
        if not checkpoint.is_file():
            entries.append(f"- `{checkpoint}` — **MISSING CHECKPOINT**")
            continue
        body = checkpoint.read_text(encoding="utf-8", errors="replace")
        anchors = anchor_pattern.findall(body)
        changed = [
            path
            for path, expected in anchors
            if sha256(repo, path) != expected
        ]
        state = "STALE" if changed else "FRESH"
        entries.append(
            f"- `{checkpoint.name}` — **{state}**, "
            f"{len(anchors)} anchors, {len(changed)} changed"
        )
        entries.extend(f"  - changed: `{path}`" for path in changed[:30])
    return "\n".join(entries) or "- No prior checkpoint supplied."


def checkpoint_delta(repo: Path, checkpoints: Iterable[Path]) -> str:
    head_pattern = re.compile(r"^- HEAD: `([0-9a-f]{7,40})`$", re.MULTILINE)
    parts: list[str] = []
    for checkpoint in checkpoints:
        if not checkpoint.is_file():
            continue
        body = checkpoint.read_text(encoding="utf-8", errors="replace")
        match = head_pattern.search(body)
        if not match:
            parts.append(f"{checkpoint.name}: no recorded HEAD")
            continue
        checkpoint_head = match.group(1)
        if not succeeds(
            ["git", "cat-file", "-e", f"{checkpoint_head}^{{commit}}"],
            cwd=repo,
        ):
            parts.append(f"{checkpoint.name}: recorded HEAD is unavailable")
            continue
        current_head = git(repo, "rev-parse", "HEAD")
        parts.append(
            f"{checkpoint.name}: `{checkpoint_head}` → `{current_head}`"
        )
        if git(repo, "rev-parse", checkpoint_head) == current_head:
            parts.append("No committed delta.")
        else:
            parts.append(
                git(
                    repo,
                    "log",
                    "--format=%h%x09%aI%x09%s",
                    f"{checkpoint_head}..HEAD",
                )
                or "No commits in range."
            )
            parts.append(git(repo, "diff", "--stat", f"{checkpoint_head}..HEAD"))
            parts.append(git(repo, "diff", "--name-status", f"{checkpoint_head}..HEAD"))
    return "\n\n".join(part for part in parts if part) or "No checkpoint delta."


def markdown_block(text: str, language: str = "text") -> str:
    return f"```{language}\n{text or '(none)'}\n```"


def search_brief(text: str) -> str:
    value = parse_json(text)
    if not isinstance(value, dict):
        return trim(text, 8_000)
    lines = []
    for item in value.get("results", [])[:15]:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "symbol":
            lines.append(
                f"- symbol `{item.get('symbol_id')}` — "
                f"{item.get('signature', item.get('kind', ''))}"
            )
        else:
            target = item.get("target_path") or item.get("title")
            lines.append(
                f"- page `{target}` — {item.get('title', '')}"
            )
    return "\n".join(lines) or "- No structural search hits."


def risk_brief(text: str) -> str:
    value = parse_json(text)
    if not isinstance(value, dict):
        return trim(text, 10_000)
    lines: list[str] = []
    for path, raw in value.get("targets", {}).items():
        if not isinstance(raw, dict):
            continue
        lines.append(f"### `{path}`")
        lines.append("")
        lines.append(f"- {raw.get('risk_summary', 'No risk summary.')}")
        lines.append(f"- Test gap: `{raw.get('test_gap', 'unknown')}`")
        partners = [
            item.get("file_path")
            for item in raw.get("co_change_partners", [])
            if isinstance(item, dict) and item.get("file_path")
        ]
        impact = [
            item.get("file_path")
            for item in raw.get("impact_surface", [])
            if isinstance(item, dict) and item.get("file_path")
        ]
        if partners:
            lines.append("- Co-change: " + ", ".join(f"`{path}`" for path in partners))
        if impact:
            lines.append("- Dependents: " + ", ".join(f"`{path}`" for path in impact))
        biomarkers = [
            f"{item.get('severity')}:{item.get('biomarker_type')}"
            for item in raw.get("top_biomarkers", [])
            if isinstance(item, dict)
        ]
        if biomarkers:
            lines.append("- Hazards: " + ", ".join(biomarkers))
        lines.append("")
    return "\n".join(lines) or "No target risk results."


def render_brief(
    *,
    repo: Path,
    task: str,
    state: GitState,
    rw: dict[str, str | list[str]],
    checkpoints: list[Path],
    evidence_path: Path | None,
) -> str:
    focus = list(rw["focus"])
    context_focus = list(rw["context_focus"])
    architecture_fresh = architecture_is_fresh(state)
    status = "FRESH" if architecture_fresh else "STALE"
    worktree_status = filtered_status(state.status)
    working_diff = git(repo, "diff", "--", *focus) if focus else ""
    evidence = str(evidence_path) if evidence_path else "(stdout / not persisted)"
    return f"""# Agent Task Brief

## Objective

{task}

## Trust gate

- HEAD: `{state.head}`
- Branch: `{state.branch}`
- Declared architecture snapshot: `{state.declared_architecture_ref or '(none)'}`
- Architecture narrative: **{status}**
- Changed files since declared architecture: **{len(state.changed_since_architecture)}**
- Architecture-relevant changed files: **{len(state.architecture_relevant_changed)}**
- Full evidence pack: `{evidence}`

Use the current code graph and the full evidence pack as structural truth.
Narrative documents are explanatory evidence only when their anchors are fresh.

## Previous checkpoint

{checkpoint_freshness(repo, checkpoints)}

{markdown_block(checkpoint_delta(repo, checkpoints))}

## Editing slice

Files with detailed smart-skeleton context:

{chr(10).join(f"- `{path}`" for path in context_focus) or "- None."}

Additional structurally related files:

{chr(10).join(f"- `{path}`" for path in focus if path not in context_focus) or "- None."}

## Best structural matches

{search_brief(str(rw["search"]))}

## Impact and hazards

{risk_brief(str(rw["risk"]))}

## Current task-focus diff

{markdown_block(working_diff or "(none)")}

## Working tree

{markdown_block(worktree_status or "(clean, excluding tool artifacts)")}

## Required agent sequence

1. Read this brief.
2. Read only the task-scoped context, rationale, and impact sections in the full
   evidence pack; expand omitted symbols only when the edit requires them.
3. State the backend/frontend contract and the files/tests expected to change.
4. Implement through the existing contract boundary.
5. Run targeted tests and typecheck.
6. Incrementally refresh RepoWise, regenerate brief/evidence, and reject any
   checkpoint or narrative claim whose anchor changed.
7. Freeze a new checkpoint with product delta, architecture delta, decisions,
   rejected approaches, verification, and open risks.
"""


def render_pack(
    *,
    repo: Path,
    task: str,
    state: GitState,
    rw: dict[str, str | list[str]],
    checkpoints: list[Path],
) -> str:
    focus = list(rw["focus"])
    declared = state.declared_architecture_ref or "(not declared)"
    changed = list(state.changed_since_architecture)
    relevant_changed = [
        path
        for path in changed
        if path in focus
        or any(
            path.startswith(prefix)
            for prefix in ("apps/web/", "apps/server/", "packages/", "docs/architecture/")
        )
        or path in {"ARCHITECTURE.md", "EXPERIMENT.md"}
    ]
    architecture_fresh = architecture_is_fresh(state)
    freshness = "FRESH" if architecture_fresh else "STALE"
    anchor_lines = "\n".join(f"- `{path}` — `{sha256(repo, path)}`" for path in focus)
    worktree_status = filtered_status(state.status)
    working_diff = git(repo, "diff", "--", *focus) if focus else ""
    staged_diff = git(repo, "diff", "--cached", "--", *focus) if focus else ""
    recent_commits = git(
        repo,
        "log",
        "--format=%h%x09%aI%x09%s",
        "-20",
        "--",
        *focus,
    ) if focus else "(no focus files)"
    changed_stat = (
        git(repo, "diff", "--stat", f"{declared}..HEAD")
        if state.architecture_ref_valid and declared != state.head
        else "(no valid architecture baseline delta)"
    )
    changed_names = "\n".join(relevant_changed[:160]) or "(none)"

    return f"""# Task Context Pack

## Task

{task}

## Snapshot identity

- Generated: `{datetime.now(timezone.utc).isoformat()}`
- Repository: `{repo}`
- Branch: `{state.branch}`
- HEAD: `{state.head}`
- Architecture document declares: `{declared}`
- Architecture freshness gate: **{freshness}**
- Files changed since declared architecture snapshot: **{len(changed)}**
- Architecture-relevant changed files: **{len(state.architecture_relevant_changed)}**

The code graph and Git state below are current at the recorded HEAD. Narrative
documents marked stale are historical evidence, not current truth.

## Freshness findings

Relevant files changed since the architecture document's declared code snapshot:

{markdown_block(changed_names)}

Diff summary since that snapshot:

{markdown_block(changed_stat)}

Current working tree:

{markdown_block(worktree_status or "(clean, excluding tool artifacts)")}

## Resolved task focus

{chr(10).join(f"- `{path}`" for path in focus) or "- No focus files resolved."}

Content-addressed anchors for this pack:

{anchor_lines or "- No anchors."}

## Prior checkpoint freshness

{checkpoint_freshness(repo, checkpoints)}

Committed delta since the supplied checkpoint:

{markdown_block(checkpoint_delta(repo, checkpoints))}

Current task-focus diff, including uncommitted edits:

{markdown_block(working_diff or "(none)")}

Current staged task-focus diff:

{markdown_block(staged_diff or "(none)")}

## Grounded task brief

{rw["answer"]}

## Current system overview

{rw["overview"]}

## Structural search evidence

{rw["search"]}

## Task-scoped code context

{rw["context"]}

## Why and historical rationale

{rw["why"]}

## Change impact and affected surface

{rw["risk"]}

## Health and change hazards

{rw["health"]}

## Relevant narrative evidence

These lines may explain intent, but must be reconciled with the freshness gate.

{markdown_block(relevant_doc_evidence(repo, task))}

## Recent commits touching the task focus

{markdown_block(recent_commits)}

## Editing protocol for the agent

1. Treat current source and the code graph as structural truth.
2. Treat narrative claims older than their anchors as hypotheses until verified.
3. Preserve the backend-owned contract; do not invent a parallel frontend model.
4. Before editing, use the impact section to enumerate required code and tests.
5. After editing, refresh the RepoWise index and regenerate this pack.
6. Compare the new pack with this one. Record product delta, architecture delta,
   decisions, rejected approaches, tests, and unresolved risks in a checkpoint.
"""


def render_checkpoint(args: argparse.Namespace, repo: Path, state: GitState) -> str:
    baseline = args.since or state.declared_architecture_ref or f"{state.head}^"
    baseline_ok = succeeds(
        ["git", "cat-file", "-e", f"{baseline}^{{commit}}"],
        cwd=repo,
    )
    if not baseline_ok:
        raise RuntimeError(f"Checkpoint baseline is not a commit: {baseline}")
    changed_files = [
        line
        for line in git(repo, "diff", "--name-only", f"{baseline}..HEAD").splitlines()
        if line
    ]
    working_files = [
        line[3:]
        for line in state.status.splitlines()
        if len(line) > 3
        and not is_tool_artifact(line[3:])
        and (repo / line[3:]).is_file()
    ]
    anchors = []
    for path in [*changed_files, *working_files]:
        if path not in anchors and (repo / path).is_file():
            anchors.append(path)
    decisions = "\n".join(f"- {item}" for item in args.decision) or "- Not recorded."
    rejected = "\n".join(f"- {item}" for item in args.rejected) or "- Not recorded."
    open_questions = (
        "\n".join(f"- {item}" for item in args.open_question)
        or "- None recorded."
    )
    tests = "\n".join(f"- {item}" for item in args.test) or "- Not recorded."
    hashes = "\n".join(f"- `{path}` — `{sha256(repo, path)}`" for path in anchors)
    commits = git(
        repo,
        "log",
        "--format=%h%x09%aI%x09%s",
        f"{baseline}..HEAD",
    )
    stat = git(repo, "diff", "--stat", f"{baseline}..HEAD")
    names = git(repo, "diff", "--name-status", f"{baseline}..HEAD")
    return f"""# Context Checkpoint — {args.title}

## Identity

- Recorded: `{datetime.now(timezone.utc).isoformat()}`
- Baseline: `{baseline}`
- HEAD: `{state.head}`
- Branch: `{state.branch}`

## Product intent

{args.intent}

## Current outcome

{args.outcome}

## Architecture delta

{args.architecture_delta}

## Decisions and invariants

{decisions}

## Rejected or failed approaches

{rejected}

## Verification evidence

{tests}

## Open questions and risks

{open_questions}

## Commits

{markdown_block(commits)}

## Changed files

{markdown_block(names)}

## Diff summary

{markdown_block(stat)}

## Content-addressed evidence

{hashes or "- No changed file anchors."}

## Working tree at checkpoint time

{markdown_block(filtered_status(state.status) or "(clean, excluding tool artifacts)")}
"""


def write_output(path: Path | None, content: str) -> None:
    if path is None:
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile a task context pack or freeze a context checkpoint."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pack = subparsers.add_parser("pack")
    pack.add_argument("--repo", type=Path, required=True)
    pack.add_argument("--task", required=True)
    pack.add_argument("--focus", action="append", default=[])
    pack.add_argument("--repowise", default=DEFAULT_REPOWISE)
    pack.add_argument("--checkpoint", type=Path, action="append", default=[])
    pack.add_argument("--output", type=Path)
    pack.add_argument("--brief-output", type=Path)

    checkpoint = subparsers.add_parser("checkpoint")
    checkpoint.add_argument("--repo", type=Path, required=True)
    checkpoint.add_argument("--title", required=True)
    checkpoint.add_argument("--intent", required=True)
    checkpoint.add_argument("--outcome", required=True)
    checkpoint.add_argument("--architecture-delta", required=True)
    checkpoint.add_argument("--since")
    checkpoint.add_argument("--decision", action="append", default=[])
    checkpoint.add_argument("--rejected", action="append", default=[])
    checkpoint.add_argument("--open-question", action="append", default=[])
    checkpoint.add_argument("--test", action="append", default=[])
    checkpoint.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    repo = args.repo.resolve()
    state = read_git_state(repo)
    if args.command == "pack":
        repowise = resolve_repowise(args.repowise)
        ensure_mcp_runtime(repowise)
        rw = asyncio.run(
            collect_repowise(
                repo=repo,
                repowise=repowise,
                task=args.task,
                explicit_focus=args.focus,
            )
        )
        checkpoints = [resolve_repo_path(repo, path) for path in args.checkpoint]
        evidence_path = (
            resolve_repo_path(repo, args.output) if args.output else None
        )
        brief_path = (
            resolve_repo_path(repo, args.brief_output)
            if args.brief_output
            else None
        )
        evidence = render_pack(
            repo=repo,
            task=args.task,
            state=state,
            rw=rw,
            checkpoints=checkpoints,
        )
        write_output(evidence_path, evidence)
        if brief_path:
            write_output(
                brief_path,
                render_brief(
                    repo=repo,
                    task=args.task,
                    state=state,
                    rw=rw,
                    checkpoints=checkpoints,
                    evidence_path=evidence_path,
                ),
            )
        return
    checkpoint_path = (
        resolve_repo_path(repo, args.output) if args.output else None
    )
    write_output(checkpoint_path, render_checkpoint(args, repo, state))


if __name__ == "__main__":
    main()
