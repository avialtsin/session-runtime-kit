#!/usr/bin/env python3
"""Где мы: проект, доска, последние сессии, рабочие деревья и вкладки Orca, установленные go-команды.
Только читает, ничего не меняет.

  python3 -I ~/.agents/skills/go-help/scripts/status.py [--dir <папка проекта>]

Работает на Python 3.9 (стоит на Mac из коробки).
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
ORCA_APP = Path("/Applications/Orca.app/Contents/Resources/bin/orca")


def run(cmd, cwd=None, timeout=30, keep_indent=False):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout.rstrip() if keep_indent else r.stdout.strip())
    except Exception as e:
        return 1, str(e)


def short(path):
    s = str(path)
    for h in (str(HOME.resolve()), str(HOME)):
        if s.startswith(h + "/"):
            return "~" + s[len(h):]
    return s


def orca_json(*args):
    o = shutil.which("orca") or (str(ORCA_APP) if ORCA_APP.exists() else None)
    if not o:
        return None
    code, text = run([o, *args, "--json"], timeout=20)
    try:
        return json.loads(text).get("result")
    except Exception:
        return None


def project(root):
    out = ["## Проект", "", "- **%s** — `%s`" % (root.name, short(root))]
    code, branch = run(["git", "-C", str(root), "branch", "--show-current"])
    if code:
        out.append("- не git-репозиторий")
        return out
    _, last = run(["git", "-C", str(root), "log", "-1", "--format=%s (%cr)"])
    out.append("- ветка `%s`; последний коммит: %s" % (branch or "без ветки", last or "нет"))
    _, dirty = run(["git", "-c", "core.quotepath=off", "-C", str(root), "status", "--porcelain"], keep_indent=True)
    files = [l[3:].strip('"') for l in dirty.splitlines() if l.strip()]
    if files:
        more = " и ещё %d" % (len(files) - 5) if len(files) > 5 else ""
        out.append("- несохранено %d: %s%s" % (len(files), ", ".join(files[:5]), more))
    code, ahead = run(["git", "-C", str(root), "rev-list", "--count", "@{u}..HEAD"])
    if not code and ahead.isdigit() and int(ahead):
        out.append("- не запушено коммитов: %s" % ahead)
    return out


def board(root):
    if not (root / "backlog").is_dir():
        return ["## Доска", "", "Доски нет — завести: `go-create-repo`."]
    if not shutil.which("backlog"):
        return ["## Доска", "", "Доска есть, но программы `backlog` нет на машине."]
    code, text = run(["backlog", "task", "list", "--plain"], cwd=str(root))
    sections, cur = {}, None
    for line in text.splitlines():
        if re.match(r"^\S.*:$", line):
            cur = line[:-1].strip()
            sections[cur] = []
        elif cur and line.strip():
            sections[cur].append(line.strip())
    out = ["## Доска", ""]
    for name, limit in (("In Progress", 10), ("To Do", 5), ("Done", 3)):
        items = sections.get(name, [])
        if items:
            title = {"In Progress": "В работе", "To Do": "Дальше", "Done": "Недавно сделано"}[name]
            more = " (ещё %d)" % (len(items) - limit) if len(items) > limit else ""
            out.append("**%s**%s:" % (title, more))
            out += ["- " + i for i in items[:limit]]
            out.append("")
    if len(out) == 2:
        out.append("Задач нет.")
    return out


def sessions(root):
    boards = sorted((root / "sessions").glob("[0-9][0-9][0-9]-*/artifacts.md"))[-3:]
    if not boards:
        return []
    out = ["## Последние сессии", ""]
    for b in reversed(boards):
        text = b.read_text(encoding="utf-8", errors="ignore")
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), b.parent.name)
        now = next((l.split("**Сейчас:**", 1)[1].strip() for l in text.splitlines() if "**Сейчас:**" in l), "")
        out.append("- `%s` — %s%s" % (b.parent.name[:3], title, (" · " + now) if now else ""))
    return out


def orca(root):
    wts = orca_json("worktree", "list")
    if wts is None:
        return ["## Orca", "", "Orca не ответила — вкладки и деревья не видно."]
    wts = wts.get("worktrees", wts) if isinstance(wts, dict) else wts
    mine = next((w for w in wts if w.get("path") == str(root)), None)
    if not mine:
        return ["## Orca", "", "Проект не подключён в Orca."]
    repo = [w for w in wts if w.get("repoId") == mine.get("repoId") and not w.get("isArchived")]
    terms = orca_json("terminal", "list") or {}
    terms = terms.get("terminals", terms) if isinstance(terms, dict) else terms
    out = ["## Orca", ""]
    for w in sorted(repo, key=lambda w: (not w.get("isMainWorktree"), w.get("path", ""))):
        tabs = [t.get("title", "") for t in terms if t.get("worktreePath") == w.get("path") and t.get("connected")]
        agents = [t for t in tabs if not re.match(r"^Terminal \d+$", t)]
        kind = "основная папка" if w.get("isMainWorktree") else "дерево"
        branch = (w.get("branch") or "").replace("refs/heads/", "")
        comment = (w.get("comment") or "").strip().splitlines()
        out.append("- %s `%s` (ветка `%s`)%s" % (kind, short(w.get("path", "")), branch,
                                                ": " + comment[0] if comment else ""))
        out += ["  - вкладка: %s" % t for t in agents[:6]]
        if len(tabs) > len(agents[:6]):
            out.append("  - и терминалов без агента: %d" % (len(tabs) - len(agents[:6])))
    return out


def commands():
    out = ["## Команды", ""]
    for skill in sorted((HOME / ".agents" / "skills").glob("go-*/SKILL.md")):
        text = skill.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"description:\s*\|?\s*\n?(.*?)\n(?:[a-z_]+:|---)", text, re.S)
        desc = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        first = re.split(r"(?<=[.;])\s", desc, maxsplit=1)[0].rstrip(".;")
        if len(first) > 130:
            first = first[:130].rsplit(" ", 1)[0] + "…"
        out.append("- `%s` — %s" % (skill.parent.name, first))
    if (HOME / ".claude" / "commands" / "handoff.md").exists():
        out.append("- `/handoff` — передать свою сессию целиком в новую (Claude Code)")
    return out


def main():
    d = Path(sys.argv[sys.argv.index("--dir") + 1]) if "--dir" in sys.argv else Path.cwd()
    code, top = run(["git", "-C", str(d), "rev-parse", "--show-toplevel"])
    root = Path(top) if not code and top else d.resolve()
    parts = [["# Где мы"], project(root), board(root), sessions(root), orca(root), commands()]
    print("\n\n".join("\n".join(p).rstrip() for p in parts if p))


if __name__ == "__main__":
    main()
