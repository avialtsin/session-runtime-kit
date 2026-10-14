#!/usr/bin/env python3
"""Refresh RepoWise and build a task-scoped brief/evidence pair."""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


PROTECTED_FILES = (
    Path(".mcp.json"),
    Path(".vscode/mcp.json"),
    Path(".vscode/extensions.json"),
)


def repowise_exclude_patterns(repo: Path) -> list[str]:
    """Load tracked RepoWise ignore rules for ingestion and query filtering."""
    ignore_file = repo / ".repowiseIgnore"
    if not ignore_file.is_file():
        return []
    return [
        line.strip()
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def repowise_excluded_roots(repo: Path) -> list[Path]:
    """Return simple top-level directories that RepoWise must not inspect."""
    roots: list[Path] = []
    for pattern in repowise_exclude_patterns(repo):
        relative = pattern.removeprefix("/").removesuffix("/")
        if (
            pattern.endswith("/")
            and relative
            and "/" not in relative
            and not any(char in relative for char in "*?![]")
        ):
            candidate = repo / relative
            if candidate.is_dir():
                roots.append(candidate)
    return roots


@contextmanager
def exclusive_repowise_refresh(repo: Path) -> Iterator[None]:
    """Prevent concurrent writers from corrupting one RepoWise index."""
    probe = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    lock_path = Path(probe.stdout.strip()) / "repowise-pipeline.lock"
    with lock_path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("Another RepoWise refresh is already running.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def hide_repowise_excluded_roots(repo: Path) -> Iterator[None]:
    """Hide ignored roots from RepoWise scanners that bypass exclude rules."""
    hidden_root = repo.parent / f".{repo.name}.repowise-hidden"
    roots = repowise_excluded_roots(repo)
    if not roots:
        yield
        return
    if hidden_root.exists():
        for hidden in sorted(hidden_root.iterdir()):
            destination = repo / hidden.name
            if destination.exists():
                raise SystemExit(
                    f"Cannot recover interrupted RepoWise refresh: both exist: "
                    f"{destination} and {hidden}"
                )
            hidden.rename(destination)
        hidden_root.rmdir()
        roots = repowise_excluded_roots(repo)

    hidden_root.mkdir(mode=0o700)
    moved: list[tuple[Path, Path]] = []
    try:
        for source in roots:
            hidden = hidden_root / source.name
            source.rename(hidden)
            moved.append((source, hidden))
        yield
    finally:
        for destination, hidden in reversed(moved):
            if hidden.exists() and not destination.exists():
                hidden.rename(destination)
        if hidden_root.is_dir() and not any(hidden_root.iterdir()):
            hidden_root.rmdir()


def resolve_executable(value: str) -> str:
    discovered = shutil.which(value)
    if discovered:
        return discovered
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    raise SystemExit(
        "RepoWise executable not found. Install it or pass "
        "--repowise /absolute/path/to/repowise."
    )


def same_path(left: Path, right: Path) -> bool:
    """Сравнить пути, игнорируя Unicode-нормализацию.

    macOS отдаёт из `git rev-parse` NFD-форму пути (`е` + U+0308), тогда как
    argv приходит в NFC (`ё`). Побайтовое сравнение таких путей ложно
    расходится в любом репозитории с не-ASCII именем каталога.
    """
    return unicodedata.normalize("NFC", str(left)) == unicodedata.normalize(
        "NFC", str(right)
    )


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    proc = subprocess.run(argv, cwd=cwd, env=env, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


@contextmanager
def protect_editor_configuration(repo: Path) -> Iterator[None]:
    snapshots: dict[Path, bytes | None] = {}
    for relative in PROTECTED_FILES:
        path = repo / relative
        snapshots[relative] = path.read_bytes() if path.is_file() else None
    try:
        yield
    finally:
        for relative, content in snapshots.items():
            path = repo / relative
            if content is None:
                if path.is_file():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        vscode = repo / ".vscode"
        if vscode.is_dir() and not any(vscode.iterdir()):
            vscode.rmdir()


def refresh_index(repo: Path, repowise: str, env: dict[str, str]) -> None:
    with (
        exclusive_repowise_refresh(repo),
        protect_editor_configuration(repo),
        hide_repowise_excluded_roots(repo),
    ):
        if (repo / ".repowise/state.json").is_file():
            run(
                [
                    repowise,
                    "update",
                    str(repo),
                    "--index-only",
                    "--no-workspace",
                    "--no-agents",
                    "--no-cost-tracking",
                    "--progress",
                    "json",
                ],
                cwd=repo,
                env=env,
            )
            return
        argv = [
            repowise,
            "init",
            str(repo),
            "--no-prose",
            "-y",
            "--no-claude-md",
            "--no-agents",
            "--no-codex",
            "--no-distill-hook",
            "--no-onboarding",
            "--no-workspace",
            "--no-cost-tracking",
            "--progress",
            "json",
        ]
        for pattern in repowise_exclude_patterns(repo):
            argv.extend(["--exclude", pattern])
        run(argv, cwd=repo, env=env)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--repo", type=Path, required=True)
    result.add_argument("--task", required=True)
    result.add_argument("--focus", action="append", default=[])
    result.add_argument("--checkpoint", type=Path, action="append", default=[])
    result.add_argument(
        "--repowise",
        default=os.environ.get("REPOWISE_BIN") or "repowise",
    )
    result.add_argument("--output-dir", type=Path)
    result.add_argument("--skip-refresh", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    repo = args.repo.resolve()
    git_probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if git_probe.returncode != 0:
        raise SystemExit(f"Not a Git repository root: {repo}")
    discovered_root = Path(git_probe.stdout.strip()).resolve()
    if not same_path(discovered_root, repo):
        raise SystemExit(
            f"--repo must be the Git repository root: {discovered_root}"
        )
    repowise = resolve_executable(args.repowise)
    environment = dict(os.environ)
    environment["DO_NOT_TRACK"] = "1"
    environment["REPOWISE_TELEMETRY_DISABLED"] = "1"
    environment["REPOWISE_SKIP_EDITOR_SETUP"] = "1"

    if not args.skip_refresh:
        refresh_index(repo, repowise, environment)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.output_dir.resolve()
        if args.output_dir
        else repo / ".context" / "packs" / stamp
    )
    output.mkdir(parents=True, exist_ok=True)
    compiler = Path(__file__).resolve().parent / "context_pipeline.py"
    argv = [
        sys.executable,
        str(compiler),
        "pack",
        "--repo",
        str(repo),
        "--task",
        args.task,
        "--repowise",
        repowise,
        "--output",
        str(output / "evidence.md"),
        "--brief-output",
        str(output / "brief.md"),
    ]
    for focus in args.focus:
        argv.extend(["--focus", focus])
    for checkpoint in args.checkpoint:
        argv.extend(["--checkpoint", str(checkpoint)])
    run(argv, cwd=repo, env=environment)
    print(f"brief: {output / 'brief.md'}")
    print(f"evidence: {output / 'evidence.md'}")


if __name__ == "__main__":
    main()
