#!/usr/bin/env python3
"""Заводит рабочее пространство проекта или дозаводит недостающее, ничего не перезаписывая.

Новые файлы, папки и симлинки создаются всегда. Существующие файлы дополняются только с --update
(недостающие разделы AGENTS.md, строки .gitignore, скрипты package.json). Обычный CLAUDE.md
становится AGENTS.md + симлинком только с --adopt-claude-md. С --dry-run ничего не пишется.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SECTION = re.compile(r"^## (.+?)\s*$", re.MULTILINE)

# Раздел с описанием проекта бывает только в новом AGENTS.md; в существующий файл его не дописываем.
IDENTITY_SECTION = "Что это"

# Базовые строки нового .gitignore.
GITIGNORE_BASE = ["node_modules/", ".DS_Store", ".env", ".env.*", "!.env.example"]
# Без этих строк в git попадут индекс, пакеты контекста и артефакты сессий; их дописываем и в
# существующий файл.
GITIGNORE_BLOCKS = [
    ("# Индекс repowise и пакеты контекста (agent-context-pipeline)", [".repowise/", ".context/packs/"]),
    ("# Табло и галереи сессий агентов (session-runtime) — временные", [".artifacts/"]),
]

BACKLOG_SCRIPTS = {
    "backlog": "backlog",
    "backlog:board": "backlog board",
    "backlog:browser": "backlog browser",
}

KEEP_DIRS = ("sessions", ".context/checkpoints")

# Доска в нашем виде: конфиг в корне, без проверки веток, без инструкций и субагента от Backlog.md.
BACKLOG_INIT_FLAGS = [
    "--defaults",
    "--integration-mode", "cli",
    "--agent-instructions", "none",
    "--install-claude-agent", "false",
    "--backlog-dir", "backlog",
    "--config-location", "root",
    "--check-branches", "false",
    "--include-remote", "false",
    "--zero-padded-ids", "3",
]

# Поколения задач: последние три цифры номера — задача, всё перед ними — поколение. Якорь TASK-1000 в
# completed/ открывает поколение 1: следующий номер CLI берёт от наибольшего в tasks/ и completed/.
# Следующие поколения открывает скилл go-grooming.
ANCHOR_FILE = "backlog/completed/task-1000 - Поколение-1-начало-нумерации.md"
ANCHOR = """---
id: TASK-1000
title: 'Поколение 1: начало нумерации'
status: Done
assignee: []
created_date: '{created}'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Служебная запись — якорь нумерации. Задачи поколения 1 идут с TASK-1001: последние три цифры — номер задачи, всё перед
ними — поколение. Якорь не архивировать и не удалять: от него CLI считает следующий номер. Поколение 1 открыто при
заведении доски.
<!-- SECTION:DESCRIPTION:END -->
"""


class Run:
    def __init__(self, target: Path, dry_run: bool, update: bool) -> None:
        self.target = target
        self.dry_run = dry_run
        self.update = update
        self.actions: list[dict[str, str]] = []

    def log(self, path: str, action: str, note: str = "") -> None:
        entry = {"path": path, "action": action}
        if note:
            entry["note"] = note
        self.actions.append(entry)

    def write(self, relative: str, content: str) -> None:
        if self.dry_run:
            return
        path = self.target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def append(self, relative: str, content: str) -> None:
        if self.dry_run:
            return
        path = self.target / relative
        current = path.read_text(encoding="utf-8")
        separator = "" if current.endswith("\n") else "\n"
        path.write_text(current + separator + "\n" + content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target", required=True, help="Абсолютный путь к папке проекта")
    parser.add_argument("--name", help="Имя репозитория (по умолчанию — имя папки)")
    parser.add_argument("--description", help="Одна строка по-русски: что это за проект")
    parser.add_argument("--purpose", help="Главный результат проекта")
    parser.add_argument("--no-git", action="store_true", help="Не заводить git")
    parser.add_argument("--update", action="store_true", help="Дописать недостающее в существующие файлы")
    parser.add_argument(
        "--adopt-claude-md",
        action="store_true",
        help="Обычный CLAUDE.md переименовать в AGENTS.md и поставить на его место симлинк",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет сделано")
    return parser.parse_args()


def fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2))
    raise SystemExit(2)


def render(template: str, values: dict[str, str]) -> str:
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def split_sections(text: str) -> dict[str, str]:
    """Разделы второго уровня: заголовок -> текст раздела вместе с заголовком."""
    matches = list(SECTION.finditer(text))
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[match.group(1)] = text[match.start():end].rstrip() + "\n"
    return result


def has_identity(values: dict[str, str]) -> bool:
    return bool(values["description"] and values["purpose"])


def package_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


def git_toplevel(path: Path) -> Path | None:
    if not path.exists():
        return None
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    return Path(completed.stdout.strip()).resolve() if completed.returncode == 0 else None


def ensure_git(run: Run, no_git: bool) -> bool:
    """Заводит git на main. Возвращает True, если папка под git."""
    if no_git:
        run.log(".git", "skipped", "--no-git")
        return False
    top = git_toplevel(run.target)
    if top == run.target:
        run.log(".git", "exists")
        return True
    if top is not None:
        run.log(".git", "skipped", f"папка внутри репозитория {top}; свой git не завожу")
        return True
    if shutil.which("git") is None:
        run.log(".git", "conflict", "нет программы git")
        return False
    if not run.dry_run:
        subprocess.run(["git", "init", "-q", "-b", "main", str(run.target)], check=True)
    run.log(".git", "created", "ветка main")
    return True


def ensure_instructions(run: Run, values: dict[str, str], adopt: bool) -> None:
    """AGENTS.md — единственный файл инструкций, CLAUDE.md — симлинк на него."""
    agents = run.target / "AGENTS.md"
    claude = run.target / "CLAUDE.md"
    adopted = False

    if claude.is_symlink():
        link = os.readlink(claude)
        if link != "AGENTS.md":
            run.log("CLAUDE.md", "conflict", f"симлинк ведёт на {link}, а должен на AGENTS.md")
    elif claude.exists():
        if agents.exists() or agents.is_symlink():
            run.log("CLAUDE.md", "conflict", "CLAUDE.md и AGENTS.md — оба обычные файлы; слить вручную в AGENTS.md")
            return
        if not adopt:
            run.log(
                "CLAUDE.md",
                "conflict",
                "обычный файл; с --adopt-claude-md он станет AGENTS.md, а CLAUDE.md — симлинком",
            )
            return
        if not run.dry_run:
            claude.rename(agents)
        run.log("AGENTS.md", "adopted", "переименован из CLAUDE.md")
        adopted = True

    template = split_sections(render("AGENTS.md", values))
    agents_ready = True
    if agents.is_symlink():
        run.log("AGENTS.md", "exists", f"симлинк на {os.readlink(agents)}")
    elif agents.exists() or adopted:
        source = agents if agents.exists() else claude
        text = source.read_text(encoding="utf-8")
        missing = [
            heading
            for heading in template
            if heading != IDENTITY_SECTION
            and not re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE)
        ]
        if not missing:
            if not adopted:
                run.log("AGENTS.md", "exists")
        elif run.update:
            run.append("AGENTS.md", "\n".join(template[heading] for heading in missing))
            run.log("AGENTS.md", "appended", "разделы: " + ", ".join(missing))
        else:
            run.log("AGENTS.md", "incomplete", "нет разделов: " + ", ".join(missing) + "; дописать — с --update")
    elif has_identity(values):
        run.write("AGENTS.md", render("AGENTS.md", values))
        run.log("AGENTS.md", "created")
    else:
        run.log("AGENTS.md", "conflict", "для нового AGENTS.md нужны --description и --purpose")
        agents_ready = False

    if agents_ready and not claude.is_symlink() and (adopted or not claude.exists()):
        if not run.dry_run:
            claude.symlink_to("AGENTS.md")
        run.log("CLAUDE.md", "linked", "-> AGENTS.md")


def ensure_readme(run: Run, values: dict[str, str]) -> None:
    if (run.target / "README.md").exists():
        run.log("README.md", "exists")
    elif has_identity(values):
        run.write("README.md", render("README.md", values))
        run.log("README.md", "created")
    else:
        run.log("README.md", "skipped", "нет --description и --purpose")


def normalize_ignore(line: str) -> str:
    return line.strip().strip("/")


def ensure_gitignore(run: Run) -> None:
    path = run.target / ".gitignore"
    if not path.exists():
        blocks = ["\n".join([header, *lines]) + "\n" for header, lines in GITIGNORE_BLOCKS]
        run.write(".gitignore", "\n".join(GITIGNORE_BASE) + "\n\n" + "\n".join(blocks))
        run.log(".gitignore", "created")
        return
    existing = {normalize_ignore(line) for line in path.read_text(encoding="utf-8").splitlines()}
    missing = [
        (header, [line for line in lines if normalize_ignore(line) not in existing])
        for header, lines in GITIGNORE_BLOCKS
    ]
    missing = [(header, lines) for header, lines in missing if lines]
    missing_lines = " ".join(line for _, lines in missing for line in lines)
    if not missing:
        run.log(".gitignore", "exists")
    elif run.update:
        run.append(".gitignore", "\n".join("\n".join([header, *lines]) + "\n" for header, lines in missing))
        run.log(".gitignore", "appended", missing_lines)
    else:
        run.log(".gitignore", "incomplete", "нет строк: " + missing_lines + "; дописать — с --update")


def detect_indent(text: str) -> str | int:
    match = re.search(r"^([ \t]+)\"", text, re.MULTILINE)
    return match.group(1) if match else 2


def ensure_package_json(run: Run, name: str, fresh: bool) -> None:
    path = run.target / "package.json"
    if not path.exists():
        if not fresh:
            run.log("package.json", "skipped", "проект уже был без package.json; доска работает и без него: backlog …")
            return
        data = {"name": package_name(name), "private": True, "scripts": dict(BACKLOG_SCRIPTS)}
        run.write("package.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        run.log("package.json", "created")
        return
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        run.log("package.json", "conflict", "не читается как JSON")
        return
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    missing = [key for key in BACKLOG_SCRIPTS if key not in scripts]
    if not missing:
        run.log("package.json", "exists")
    elif run.update:
        data["scripts"] = {**scripts, **{key: BACKLOG_SCRIPTS[key] for key in missing}}
        run.write("package.json", json.dumps(data, ensure_ascii=False, indent=detect_indent(text)) + "\n")
        run.log("package.json", "appended", "скрипты: " + ", ".join(missing))
    else:
        run.log("package.json", "incomplete", "нет скриптов: " + ", ".join(missing) + "; дописать — с --update")


def ensure_dirs(run: Run) -> None:
    for relative in KEEP_DIRS:
        if (run.target / relative).exists():
            run.log(relative + "/", "exists")
            continue
        run.write(relative + "/.gitkeep", "")
        run.log(relative + "/", "created")


def ensure_backlog(run: Run, name: str, under_git: bool) -> None:
    configs = [run.target / "backlog.config.yml", run.target / "backlog" / "config.yml", run.target / ".backlog"]
    if any(path.exists() for path in configs):
        run.log("backlog/", "exists")
        return
    if (run.target / "backlog").exists():
        run.log("backlog/", "conflict", "папка backlog/ есть, а конфига доски нет — разобрать вручную")
        return
    if shutil.which("backlog") is None:
        run.log("backlog/", "conflict", "нет программы backlog; поставить один раз на машину: bun add -g backlog.md")
        return
    command = ["backlog", "init", name, *BACKLOG_INIT_FLAGS]
    if not under_git:
        command.append("--no-git")
    if run.dry_run:
        run.log("backlog/", "created", "backlog init")
        run.log(ANCHOR_FILE, "created", "якорь поколения 1: задачи пойдут с TASK-1001")
        return
    env = os.environ.copy()
    env.pop("BACKLOG_CWD", None)
    completed = subprocess.run(command, cwd=run.target, capture_output=True, text=True, env=env)
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout).strip().splitlines()[-5:]
        run.log("backlog/", "conflict", "backlog init упал: " + " | ".join(tail))
        return
    run.log("backlog/", "created", "доска Backlog.md, конфиг backlog.config.yml")
    run.write(ANCHOR_FILE, ANCHOR.format(created=f"{datetime.now():%Y-%m-%d %H:%M}"))
    run.log(ANCHOR_FILE, "created", "якорь поколения 1: задачи пойдут с TASK-1001")


def main() -> None:
    args = parse_args()
    raw = Path(args.target).expanduser()
    if not raw.is_absolute():
        fail("--target должен быть абсолютным путём")
    if raw.is_symlink():
        fail("--target — симлинк; укажите настоящую папку")
    if raw.exists() and not raw.is_dir():
        fail("--target — не папка")
    target = raw.resolve()
    name = (args.name or target.name).strip()
    if not REPO_NAME.match(name):
        fail(f"имя «{name}» не подходит: латиница, цифры, точка, дефис, подчёркивание; задайте --name")

    # Свежая папка — нет ничего, кроме служебного; в ней заводим и package.json.
    fresh = not target.exists() or not any(p.name not in (".DS_Store", ".git") for p in target.iterdir())
    values = {
        "name": name,
        "description": (args.description or "").strip(),
        "purpose": (args.purpose or "").strip(),
    }
    run = Run(target, args.dry_run, args.update)
    if not args.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    under_git = ensure_git(run, args.no_git)
    ensure_instructions(run, values, args.adopt_claude_md)
    ensure_readme(run, values)
    ensure_gitignore(run)
    ensure_package_json(run, name, fresh)
    ensure_dirs(run)
    ensure_backlog(run, name, under_git)

    conflicts = [action for action in run.actions if action["action"] == "conflict"]
    report: dict[str, object] = {
        "ok": not conflicts,
        "dry_run": args.dry_run,
        "target": str(target),
        "name": name,
        "fresh": fresh,
        "actions": run.actions,
    }
    if any(action["action"] == "incomplete" for action in run.actions):
        report["needs_update"] = "в существующих файлах не хватает частей — перезапустить с --update"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if conflicts else 0)


if __name__ == "__main__":
    main()
