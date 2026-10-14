#!/usr/bin/env python3
"""Поколения задач на доске Backlog.md.

Номер задачи: последние три цифры — задача, всё перед ними — поколение. TASK-3001 — поколение 3,
задача 001; подзадача — TASK-3001.01. Поколение открывает якорь
backlog/completed/task-N000 - Поколение-N-начало-нумерации.md: ключа номера у CLI нет, следующий
номер он берёт от наибольшего в tasks/ и completed/ (архив не считает).

  generation.py status                         — якоря, наибольший номер, активные задачи по поколениям
  generation.py open [--generation N]          — положить якорь следующего поколения
  generation.py retire --map map.json          — старые задачи: заметка «Поколение N: …» и архив через CLI

Запуск из корня репозитория или с --root. open и retire с --dry-run ничего не меняют.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

PER_GENERATION = 1000
# Сколько задач поколения занято, когда пора открывать следующее: после N999 CLI выдаст N+1000.
CROWDED = 900


class Board:
    def __init__(self, root: Path) -> None:
        config = next((p for p in (root / "backlog.config.yml", root / "backlog" / "config.yml") if p.exists()), None)
        if config is None:
            raise SystemExit(f"в {root} нет доски: ни backlog.config.yml, ни backlog/config.yml")
        text = config.read_text(encoding="utf-8")
        self.root = root
        self.dir = root / self.setting(text, "backlog_directory", "backlog")
        self.prefix = self.setting(text, "task_prefix", "task").upper()
        self.file_id = re.compile(rf"^{re.escape(self.prefix.lower())}-(\d+)((?:\.\d+)*) - ", re.IGNORECASE)

    @staticmethod
    def setting(text: str, key: str, default: str) -> str:
        match = re.search(rf"^{key}:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", text, re.MULTILINE)
        return match.group(1) if match else default

    def tasks(self, folder: str) -> list[dict]:
        """Задачи папки: id, номер верхнего уровня, статус, имя файла."""
        result = []
        path = self.dir / folder
        if not path.is_dir():
            return result
        for file in sorted(path.glob("*.md")):
            match = self.file_id.match(file.name)
            if not match:
                continue
            head = file.read_text(encoding="utf-8").split("\n---", 1)[0]
            status = re.search(r"^status:\s*['\"]?(.+?)['\"]?\s*$", head, re.MULTILINE)
            result.append({
                "id": f"{self.prefix}-{match.group(1)}{match.group(2)}",
                "number": int(match.group(1)),
                "status": status.group(1) if status else "?",
                "file": file,
            })
        return result

    def anchors(self) -> list[dict]:
        return [t for t in self.tasks("completed")
                if t["number"] >= PER_GENERATION and t["number"] % PER_GENERATION == 0 and "Поколение" in t["file"].name]

    def cli(self, *args: str) -> str:
        env = os.environ.copy()
        env.pop("BACKLOG_CWD", None)
        out = subprocess.run(["backlog", *args], cwd=self.root, capture_output=True, text=True, env=env)
        if out.returncode != 0:
            raise SystemExit(f"backlog {' '.join(args[:3])}: {(out.stderr or out.stdout).strip()}")
        return out.stdout


def generation_of(number: int) -> int:
    return number // PER_GENERATION


def label(board: Board, number: int) -> str:
    return f"{board.prefix}-{number:03d}"


def status(board: Board) -> None:
    active, done, archived = board.tasks("tasks"), board.tasks("completed"), board.tasks("archive/tasks")
    anchors = board.anchors()
    numbered = active + done
    top = max((t["number"] for t in numbered), default=0)
    top_all = max((t["number"] for t in numbered + archived), default=0)
    current = generation_of(max(t["number"] for t in anchors)) if anchors else None

    print(f"Доска: {board.dir} (префикс {board.prefix})")
    if current is None:
        print("Поколений нет: якоря в completed/ не найдено. Первое откроет груминг.")
    else:
        print("Якоря: " + ", ".join(label(board, t["number"]) for t in anchors) + f". Текущее поколение — {current}.")
    print(f"Следующий номер CLI: {label(board, top + 1)} (наибольший в tasks/ и completed/ — {label(board, top)}).")
    if top_all > top:
        print(f"В архиве есть номер больше: {label(board, top_all)}. Новое поколение — только выше него.")

    by_generation: dict[int, Counter] = defaultdict(Counter)
    for task in active:
        by_generation[generation_of(task["number"])][task["status"]] += 1
    print(f"Активные задачи (tasks/): {len(active)}")
    for gen in sorted(by_generation):
        counts = by_generation[gen]
        detail = ", ".join(f"{name} {count}" for name, count in counts.most_common())
        name = f"поколение {gen}" if gen else f"номера до {PER_GENERATION} (до поколений)"
        mark = " ← текущее" if gen == current else ""
        print(f"  {name}: {sum(counts.values())} — {detail}{mark}")

    if current is not None:
        used = top % PER_GENERATION if generation_of(top) == current else 0
        print(f"Занято номеров в поколении {current}: {used} из {PER_GENERATION - 1}.")
        if generation_of(top) > current:
            print(f"ВНИМАНИЕ: номер {label(board, top)} ушёл за поколение {current} — открыть новое поколение грумингом.")
        elif used >= CROWDED:
            print("ВНИМАНИЕ: поколение почти заполнено — пора груминг и новое поколение.")
        old = sum(sum(c.values()) for g, c in by_generation.items() if g < current)
        if old:
            print(f"Активных задач прошлых поколений: {old} — их ведут параллельные сессии или забыли переписать.")
    print(f"Следующее поколение: {generation_of(top_all) + 1} (якорь {label(board, (generation_of(top_all) + 1) * PER_GENERATION)}).")


def anchor_text(board: Board, gen: int, note: str) -> str:
    first = label(board, gen * PER_GENERATION + 1)
    description = (
        f"Служебная запись — якорь нумерации. Задачи поколения {gen} идут с {first}: последние три цифры — "
        f"номер задачи, всё перед ними — поколение. Якорь не архивировать и не удалять: от него CLI считает "
        f"следующий номер."
    )
    if note:
        description += "\n\n" + note
    return (
        "---\n"
        f"id: {label(board, gen * PER_GENERATION)}\n"
        f"title: 'Поколение {gen}: начало нумерации'\n"
        "status: Done\n"
        "assignee: []\n"
        f"created_date: '{datetime.now():%Y-%m-%d %H:%M}'\n"
        "labels: []\n"
        "dependencies: []\n"
        "---\n\n"
        "## Description\n\n"
        "<!-- SECTION:DESCRIPTION:BEGIN -->\n"
        f"{description}\n"
        "<!-- SECTION:DESCRIPTION:END -->\n"
    )


def open_generation(board: Board, gen: int | None, note: str, dry_run: bool) -> None:
    every = board.tasks("tasks") + board.tasks("completed") + board.tasks("archive/tasks")
    top = max((t["number"] for t in every), default=0)
    gen = gen if gen is not None else generation_of(top) + 1
    anchor = gen * PER_GENERATION
    if anchor <= top:
        raise SystemExit(f"поколение {gen} не открыть: якорь {label(board, anchor)} не больше занятого номера "
                         f"{label(board, top)}; нужно поколение от {generation_of(top) + 1}")
    path = board.dir / "completed" / f"{board.prefix.lower()}-{anchor} - Поколение-{gen}-начало-нумерации.md"
    if dry_run:
        print(f"будет создан якорь: {path.relative_to(board.root)}; первая задача — {label(board, anchor + 1)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(anchor_text(board, gen, note), encoding="utf-8")
    board.cli("task", "view", label(board, anchor), "--plain")
    print(f"якорь: {path.relative_to(board.root)}; первая задача поколения — {label(board, anchor + 1)}")
    print("Дальше сразу: закоммитить якорь отдельным коммитом и запушить, чтобы параллельные сессии "
          f"тоже нумеровали с {label(board, anchor + 1)}.")


def retire(board: Board, map_path: Path, gen: int | None, cascade: bool, dry_run: bool) -> None:
    """Старым задачам — заметка о том, куда они переписаны, и архив. Подзадачи архив не уносит сам."""
    plan: dict[str, list[str] | str] = json.loads(map_path.read_text(encoding="utf-8"))
    anchors = board.anchors()
    if gen is None:
        if not anchors:
            raise SystemExit("якоря нет: сначала generation.py open")
        gen = generation_of(max(t["number"] for t in anchors))
    active = {t["id"]: t for t in board.tasks("tasks")}
    known = set(active) | {t["id"] for t in board.tasks("completed")}

    problems = []
    for old, target in plan.items():
        if old not in active:
            problems.append(f"{old}: нет среди активных задач")
        if isinstance(target, list):
            problems += [f"{old} → {new}: такой задачи нет" for new in target if new not in known]
            if not target:
                problems.append(f"{old}: пустой список — укажите новые задачи или причину строкой")
    orphans = {}
    for sub in active:
        parents = [old for old in plan if sub.startswith(old + ".")]
        if parents and sub not in plan:
            orphans[sub] = max(parents, key=len)
    if orphans and not cascade:
        problems.append("подзадачи без решения (добавьте в карту или запустите с --cascade): " + ", ".join(orphans))
    if problems:
        raise SystemExit("карта не годится:\n  " + "\n  ".join(problems))

    today = f"{datetime.now():%Y-%m-%d}"
    notes = {}
    for old, target in plan.items():
        reason = f"переформулировано в {', '.join(target)}" if isinstance(target, list) else target.rstrip(".")
        notes[old] = f"Поколение {gen} ({today}): {reason}."
    for sub, old in orphans.items():
        notes[sub] = f"Поколение {gen} ({today}): вместе с {old} — {notes[old].split(': ', 1)[1]}"

    for old in sorted(notes, key=lambda i: (-i.count("."), i)):
        print(f"{old}: {notes[old]}")
        if not dry_run:
            board.cli("task", "edit", old, "--append-notes", notes[old])
            board.cli("task", "archive", old)
    print(("будет заархивировано" if dry_run else "заархивировано") + f": {len(notes)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="Корень репозитория с доской")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Поколения на доске")
    opener = sub.add_parser("open", help="Положить якорь следующего поколения")
    opener.add_argument("--generation", type=int, help="Номер поколения (по умолчанию — следующий)")
    opener.add_argument("--note", default="", help="Строка в якорь: когда и каким грумингом открыто")
    opener.add_argument("--dry-run", action="store_true")
    retirer = sub.add_parser("retire", help="Старые задачи — заметка и архив")
    retirer.add_argument("--map", required=True, type=Path,
                         help='JSON: {"TASK-12": ["TASK-3001"], "TASK-40": "устарело: функцию убрали"}')
    retirer.add_argument("--generation", type=int, help="Поколение для заметки (по умолчанию — текущее)")
    retirer.add_argument("--cascade", action="store_true", help="Подзадачи без решения — вслед за родителем")
    retirer.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    board = Board(Path(args.root).expanduser().resolve())
    if args.command == "status":
        status(board)
    elif args.command == "open":
        open_generation(board, args.generation, args.note, args.dry_run)
    else:
        retire(board, args.map, args.generation, args.cascade, args.dry_run)


if __name__ == "__main__":
    main()
