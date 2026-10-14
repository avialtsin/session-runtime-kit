#!/usr/bin/env python3
"""Доктор всего хозяйства: машина (установка session-runtime, правила, скиллы, инструменты) и все
проекты из Orca одной таблицей. Только смотрит, ничего не меняет.

  python3 -I ~/.agents/skills/go-doctor/scripts/doctor.py [--out отчёт.md]

Работает на Python 3.9 (стоит на Mac из коробки).
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
SKILLS = (HOME / ".agents" / "skills", HOME / ".claude" / "skills")
ORCA_APP = Path("/Applications/Orca.app/Contents/Resources/bin/orca")


def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except Exception as e:  # нет программы, завис
        return 1, str(e)


def orca():
    return shutil.which("orca") or (str(ORCA_APP) if ORCA_APP.exists() else None)


# ---------- машина ----------

def machine():
    out, bad, fixes = [], [], set()
    art = HOME / ".agents" / "skills" / "session-artifacts"
    pkg = art.resolve().parent.parent if art.is_symlink() else None
    if not pkg or not (pkg / "install.py").exists():
        bad.append("session-runtime не установлен: нет ссылки ~/.agents/skills/session-artifacts")
    else:
        out.append("Пакет: `%s`" % str(pkg).replace(str(HOME), "~"))
        code, text = run([sys.executable, str(pkg / "install.py"), "--dry-run"])
        pending = [l for l in text.splitlines()[1:] if l.strip() and not l.startswith("есть")]
        fixes = {l.split()[1] for l in pending if l.startswith("убрано")}  # битые ссылки, которые уберёт установка
        for l in pending:
            l = re.sub(r"\s+", " ", l).strip()
            if l.startswith("НЕТ") and any(w in l for w in ("claude", "codex")):
                out.append("нет " + l.split()[1] + " (по желанию)")
            else:
                bad.append("установка: " + l)
        if pending and any(l.split()[0] in ("создано", "заменено", "обновлено", "дописано", "убрано") for l in pending):
            bad.append("установка отстала от пакета — `python3 %s/install.py`" % str(pkg).replace(str(HOME), "~"))
    for base in SKILLS:
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            if p.is_symlink() and not p.exists() and str(p).replace(str(HOME), "~") not in fixes:
                bad.append("битая ссылка скилла: `%s` → %s" % (str(p).replace(str(HOME), "~"), os.readlink(p)))
    claude, agents = SKILLS[1], SKILLS[0]
    if claude.is_dir():
        for p in sorted(claude.iterdir()):
            if p.is_dir() and not p.is_symlink() and (agents / p.name).exists():
                bad.append("копия вместо ссылки: `~/.claude/skills/%s` (оригинал — ~/.agents/skills)" % p.name)
    if sys.version_info < (3, 9):
        bad.append("Python %d.%d — нужен 3.9 или новее" % sys.version_info[:2])
    for tool, why in (("gh", "GitHub из go-create-repo"), ("repowise", "контекст кода, agent-context-pipeline")):
        if not shutil.which(tool):
            out.append("нет %s (по желанию: %s)" % (tool, why))
    return out, bad


# ---------- проекты ----------

def skill_exists(name):
    return any((b / name).exists() for b in SKILLS)


def stale_skills(agents_md):
    # Упоминания скиллов, которых на машине нет: «скилл `x`», «скиллом `x`», «скилла `x`».
    try:
        text = agents_md.read_text(encoding="utf-8")
    except Exception:
        return []
    names = re.findall(r"скилл\w*\s+`([a-z][a-z0-9-]+)`", text)
    return sorted({n for n in names if not skill_exists(n)})


def project(path, base_ref):
    p = Path(path)
    row = {"name": p.name, "path": str(p).replace(str(HOME), "~"), "notes": []}
    if not p.is_dir():
        row["notes"].append("папки нет — убрать из Orca или вернуть")
        row.update(agents="?", board="?", sessions="?", git="?", base="?")
        return row
    a, c = p / "AGENTS.md", p / "CLAUDE.md"
    if a.exists() and c.is_symlink() and os.readlink(c) == "AGENTS.md":
        row["agents"] = "✓"
    elif a.exists() and not c.exists():
        row["agents"] = "⚠"; row["notes"].append("нет ссылки CLAUDE.md → AGENTS.md")
    elif c.exists() and not c.is_symlink():
        row["agents"] = "⚠"; row["notes"].append("CLAUDE.md — обычный файл, а не ссылка на AGENTS.md")
    else:
        row["agents"] = "—"; row["notes"].append("нет AGENTS.md")
    stale = stale_skills(a) if a.exists() else []
    if stale:
        row["notes"].append("AGENTS.md упоминает скиллы, которых нет: " + ", ".join("`%s`" % s for s in stale))
    if (p / "backlog").is_dir() and (p / "backlog.config.yml").exists():
        anchors = list((p / "backlog" / "completed").glob("task-*000 - *")) if (p / "backlog" / "completed").is_dir() else []
        row["board"] = "✓" if anchors else "⚠"
        if not anchors:
            row["notes"].append("доска без поколений (нет якоря task-N000)")
    else:
        row["board"] = "—"; row["notes"].append("нет доски Backlog.md")
    row["sessions"] = "✓" if (p / "sessions").is_dir() else "—"
    if row["sessions"] == "—":
        row["notes"].append("нет sessions/")
    ign = p / ".gitignore"
    if not (ign.exists() and ".artifacts/" in ign.read_text(encoding="utf-8", errors="ignore").split()):
        row["notes"].append(".artifacts/ не в .gitignore")
    code, top = run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], timeout=10)
    if code:
        row["git"] = "—"; row["notes"].append("не git")
    else:
        _, branch = run(["git", "-C", str(p), "branch", "--show-current"], timeout=10)
        _, dirty = run(["git", "-C", str(p), "status", "--porcelain"], timeout=20)
        n_dirty = len([l for l in dirty.splitlines() if l.strip()])
        code, ahead = run(["git", "-C", str(p), "rev-list", "--count", "@{u}..HEAD"], timeout=10)
        parts = [branch or "без ветки"]
        if n_dirty:
            parts.append("несохранено %d" % n_dirty)
        _, remotes = run(["git", "-C", str(p), "remote"], timeout=10)
        if not remotes:
            parts.append("без GitHub")
        elif code:
            parts.append("ветка не связана с GitHub")
        elif ahead.isdigit() and int(ahead):
            parts.append("не запушено %s" % ahead)
        row["git"] = ", ".join(parts)
    row["base"] = "✓" if base_ref in ("main", "refs/heads/main") else (base_ref or "origin/main")
    if row["base"] != "✓":
        row["notes"].append("деревья Orca от %s, а не от локального main" % row["base"])
    return row


def projects():
    o = orca()
    if not o:
        return None, "Orca не найдена — список проектов брать неоткуда"
    code, text = run([o, "repo", "list", "--json"], timeout=30)
    try:
        data = json.loads(text)
        repos = data.get("result", data)
        repos = repos.get("repos", repos) if isinstance(repos, dict) else repos
    except Exception:
        return None, "Orca не ответила (`orca repo list`): открой Orca и повтори"
    rows = [project(r.get("path", ""), r.get("worktreeBaseRef", "")) for r in repos if r.get("path")]
    return rows, None


def main():
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    info, bad = machine()
    lines = ["# Доктор: машина и проекты", "", "## Машина", ""]
    lines += ["- " + l for l in info]
    lines += ["- ✗ " + l for l in bad] or []
    if not bad:
        lines.append("- ✓ установка, правила, ссылки скиллов и инструменты в порядке")
    rows, err = projects()
    lines += ["", "## Проекты", ""]
    if err:
        lines.append(err)
    else:
        lines += ["| проект | AGENTS.md | доска | sessions | git | деревья от main |", "|---|---|---|---|---|---|"]
        for r in sorted(rows, key=lambda r: (-len(r["notes"]), r["name"])):
            lines.append("| %s | %s | %s | %s | %s | %s |" % (r["name"], r["agents"], r["board"], r["sessions"], r["git"], r["base"]))
        lines += ["", "### Что не так", ""]
        problems = [r for r in rows if r["notes"]]
        for r in sorted(problems, key=lambda r: r["name"]):
            lines.append("- **%s** (`%s`): %s" % (r["name"], r["path"], "; ".join(r["notes"])))
        if not problems:
            lines.append("Все проекты в порядке.")
    text = "\n".join(lines) + "\n"
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
        print(out_path)
    else:
        print(text)


if __name__ == "__main__":
    main()
