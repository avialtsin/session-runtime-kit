#!/usr/bin/env python3
"""Развернуть session-runtime на машине. Повторный запуск обновляет, лишнего не трогает.

  python3 install.py            поставить / обновить
  python3 install.py --dry-run  только показать, что изменится

Что делает:
  - скиллы skills/* — ссылками в ~/.agents/skills (Codex) и ~/.claude/skills (Claude);
  - команда artifacts и команды скиллов (skills/<скилл>/<скилл>, исполняемый) — ссылками в ~/.local/bin;
  - команды commands/*.md — ссылками в ~/.claude/commands;
  - правила rules/global.md — блоком между метками в ~/.agents/AGENTS.md (CLAUDE.md и AGENTS.md
    Codex — ссылки на него);
  - Claude Code: строка внизу вкладки и хуки табло в ~/.claude/settings.json;
  - .artifacts/ — в глобальный git ignore.
Всё, что заменяется, сначала копируется в ~/.agents/session-runtime/backup/<дата>/.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent
HOME = Path.home()
LOCAL = HOME / ".agents" / "session-runtime"  # своё для этой машины: прежняя строка, копии
BACKUP = LOCAL / "backup" / datetime.now().strftime("%y%m%d-%H%M%S")
BEGIN = "<!-- session-runtime:begin"
END = "<!-- session-runtime:end -->"
ARTIFACTS = '"$HOME/.agents/skills/session-artifacts/artifacts"'
STATUSLINE = 'bash "$HOME/.agents/skills/session-artifacts/statusline.sh"'
HOOKS = [("UserPromptSubmit", None, "prompt", {}), ("SessionStart", None, "start", {}),
         # перед командой — только «что агент делает сейчас» для строки внизу; в фоне, сессию не держит
         ("PreToolUse", "Bash|Edit|Write|MultiEdit|NotebookEdit", "pre", {"async": True}),
         ("PostToolUse", "Bash|Edit|Write|MultiEdit|NotebookEdit", "tool", {}),
         # кадр, который агент посмотрел, — в галерею; в фоне, агента не задерживает
         ("PostToolUse", "Read", "tool", {"async": True}), ("Stop", None, "stop", {})]

DRY = "--dry-run" in sys.argv
report = []


def say(what, path, note=""):
    report.append(f"{what:<10} {str(path).replace(str(HOME), '~')}" + (f" — {note}" if note else ""))


def backup(path):
    if DRY or not (path.exists() or path.is_symlink()):
        return
    dst = BACKUP / path.relative_to(HOME)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        dst.write_text(f"symlink -> {os.readlink(path)}\n", encoding="utf-8")
    elif path.is_dir():
        shutil.copytree(path, dst, symlinks=True)
    else:
        shutil.copy2(path, dst)


def link(dst, target):
    # dst -> target; на месте настоящей папки или чужой ссылки — копия в backup и ссылка.
    if dst.is_symlink() and os.readlink(dst) == str(target):
        return say("есть", dst)
    if dst.exists() or dst.is_symlink():
        backup(dst)
        if not DRY:
            shutil.rmtree(dst) if dst.is_dir() and not dst.is_symlink() else dst.unlink()
        say("заменено", dst, f"-> {target}")
    else:
        say("создано", dst, f"-> {target}")
    if not DRY:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(target)


def prune_skill_links():
    # Скилл убран из skills/ (свёрнут в архив) — его ссылки на машине ведут в пустоту; убрать только свои.
    ours = str(REPO / "skills") + os.sep
    for dst in sorted((HOME / ".agents" / "skills").glob("*")):
        if dst.is_symlink() and os.readlink(dst).startswith(ours) and not dst.exists():
            backup(dst)
            if not DRY:
                dst.unlink()
            say("убрано", dst, "скилла больше нет в session-runtime")
            claude = HOME / ".claude" / "skills" / dst.name
            if claude.is_symlink() and os.readlink(claude) == str(Path("../../.agents/skills") / dst.name):
                backup(claude)
                if not DRY:
                    claude.unlink()
                say("убрано", claude)
    for dst in sorted((HOME / ".local" / "bin").glob("*")):
        if dst.is_symlink() and os.readlink(dst).startswith(ours) and not dst.exists():
            backup(dst)
            if not DRY:
                dst.unlink()
            say("убрано", dst, "команды больше нет в session-runtime")


def install_links():
    for d in sorted((REPO / "skills").iterdir()):
        if (d / "SKILL.md").exists():
            link(HOME / ".agents" / "skills" / d.name, d)
            link(HOME / ".claude" / "skills" / d.name, Path("../../.agents/skills") / d.name)
            # Команда скилла — исполняемый файл с его именем (<скилл>/<скилл>) — в ~/.local/bin.
            cmd = d / d.name
            if cmd.is_file() and os.access(cmd, os.X_OK):
                link(HOME / ".local" / "bin" / d.name, cmd)
    prune_skill_links()
    for f in sorted((REPO / "commands").glob("*.md")):
        link(HOME / ".claude" / "commands" / f.name, f)
    # Прежний путь скрипта (07.10): на него ещё смотрят открытые сессии.
    link(HOME / ".agents" / "artifacts", REPO / "skills" / "session-artifacts")
    # Команда `artifacts` — для агентов в любом терминале; ~/.local/bin должен быть в PATH.
    link(HOME / ".local" / "bin" / "artifacts", REPO / "skills" / "session-artifacts" / "artifacts")
    if str(HOME / ".local" / "bin") not in os.environ.get("PATH", "").split(":"):
        say("ВНИМАНИЕ", HOME / ".local" / "bin", "не в PATH — добавьте в ~/.zshrc: export PATH=\"$HOME/.local/bin:$PATH\"")


def install_rules():
    agents = HOME / ".agents" / "AGENTS.md"
    src = (REPO / "rules" / "global.md").read_text(encoding="utf-8")
    body = src[src.index("\n## ") + 1:].rstrip()
    where = f"~/{REPO.relative_to(HOME)}" if REPO.is_relative_to(HOME) else str(REPO)
    block = f"{BEGIN} — блок ставит {where}/install.py из rules/global.md; править там -->\n\n{body}\n\n{END}\n"
    text = agents.read_text(encoding="utf-8") if agents.exists() else "# Global Rules\n\n"
    if BEGIN in text and END in text:
        a, b = text.index(BEGIN), text.index(END) + len(END) + 1
        new = text[:a] + block + text[b:]
    else:
        # Первая установка: те же разделы вне блока — прежние копии правил, блок встаёт на их место.
        titles = [l[3:].strip() for l in body.splitlines() if l.startswith("## ")]
        parts = text.split("\n## ")
        kept, pos = [parts[0]], None
        for p in parts[1:]:
            if p.splitlines()[0].strip() in titles:
                pos = len(kept) if pos is None else pos
                continue
            kept.append(p)
        if pos is None:
            new = text.rstrip() + "\n\n" + block
        else:
            kept.insert(pos, block)
            new = kept[0] + "".join("\n## " + p if not p.startswith(BEGIN) else "\n" + p for p in kept[1:])
            new = new.replace("\n\n\n", "\n\n").rstrip() + "\n"
    if new == text:
        say("есть", agents, "правила")
    else:
        backup(agents)
        if not DRY:
            agents.parent.mkdir(parents=True, exist_ok=True)
            agents.write_text(new, encoding="utf-8")
        say("обновлено", agents, "блок правил session-runtime")
    for f in (HOME / ".claude" / "CLAUDE.md", HOME / ".codex" / "AGENTS.md"):
        if f.is_symlink() or not f.exists():
            link(f, Path("../.agents/AGENTS.md"))
        else:
            say("ВНИМАНИЕ", f, "обычный файл, а не ссылка на ~/.agents/AGENTS.md — слить руками")


def ours(cmd):
    return "artifacts" in cmd and ("hook" in cmd or "statusline" in cmd)


def install_settings():
    f = HOME / ".claude" / "settings.json"
    s = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    old = json.dumps(s, sort_keys=True)
    sl = s.get("statusLine") or {}
    prev = sl.get("command", "")
    if prev and not ours(prev):
        # Прежняя строка машины остаётся над табло: её вызывает statusline.sh.
        if not DRY:
            LOCAL.mkdir(parents=True, exist_ok=True)
            (LOCAL / "statusline-before.sh").write_text(
                "#!/bin/bash\n# Прежняя строка состояния этой машины; её вызывает statusline.sh session-runtime.\n"
                + prev + "\n", encoding="utf-8")
        say("сохранено", LOCAL / "statusline-before.sh", "прежняя строка состояния")
    s["statusLine"] = {**sl, "type": "command", "command": STATUSLINE, "padding": sl.get("padding", 0),
                       "refreshInterval": sl.get("refreshInterval", 5)}
    hooks = s.setdefault("hooks", {})
    for event in dict.fromkeys(e for e, *_ in HOOKS):
        want = [(matcher, {"type": "command", "command": f"python3 {ARTIFACTS} hook {arg}", "timeout": 10, **extra})
                for e, matcher, arg, extra in HOOKS if e == event]
        mine = [(g.get("matcher"), h) for g in hooks.get(event, []) for h in g.get("hooks", [])
                if ours(h.get("command", ""))]
        if mine == want:
            continue  # уже стоит как надо; соседние хуки (Orca) могли встать после — порядок не трогаем
        groups = []
        for g in hooks.get(event, []):
            g = {**g, "hooks": [h for h in g.get("hooks", []) if not ours(h.get("command", ""))]}
            if g["hooks"]:
                groups.append(g)
        hooks[event] = groups + [{**({"matcher": m} if m else {}), "hooks": [h]} for m, h in want]
    if json.dumps(s, sort_keys=True) == old:
        return say("есть", f, "строка внизу и хуки")
    backup(f)
    if not DRY:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    say("обновлено", f, "строка внизу и хуки табло")


def install_gitignore():
    path = subprocess.run(["git", "config", "--global", "core.excludesFile"], capture_output=True,
                          text=True).stdout.strip()
    f = Path(os.path.expanduser(path)) if path else HOME / ".config" / "git" / "ignore"
    text = f.read_text(encoding="utf-8") if f.exists() else ""
    if ".artifacts/" in text.split():
        return say("есть", f, ".artifacts/")
    if not DRY:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text.rstrip() + ("\n\n" if text.strip() else "")
                     + "# Табло и галереи сессий агентов (session-runtime) — временные\n.artifacts/\n",
                     encoding="utf-8")
    say("дописано", f, ".artifacts/")


def check_tools():
    for tool, why in [("git", "обязательно"), ("claude", "Claude Code"), ("orca", "вкладки и деревья"),
                      ("backlog", "доска задач: npm i -g backlog.md"), ("codex", "по желанию")]:
        say("есть" if shutil.which(tool) else "НЕТ", tool, why)
    # Команды Orca для агента (вкладки, деревья, браузер) — скиллы самой Orca, ставит она.
    if shutil.which("orca") and not (HOME / ".agents" / "skills" / "orca-cli").exists():
        say("НЕТ", "скилл orca-cli", "orca skills install --skill orca-cli")


def main():
    install_links()
    install_rules()
    install_settings()
    install_gitignore()
    check_tools()
    print(("Только показ (--dry-run):\n" if DRY else "") + "\n".join(report))
    if not DRY and BACKUP.exists():
        print(f"Копии заменённого: {BACKUP}")


if __name__ == "__main__":
    main()
