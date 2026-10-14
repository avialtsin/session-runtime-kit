#!/bin/bash
# Поставить session-runtime-kit на Mac одной командой — с нуля или поверх, повторный запуск обновляет:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/avialtsin/session-runtime-kit/main/setup.sh)"
# Всё — в домашнюю папку, без пароля: доска Backlog.md (готовый файл с GitHub), скилл orca-cli (из самой
# Orca), пакет в ~/session-runtime-kit и его install.py. Уже стоящее не трогает.
set -euo pipefail

REPO_URL="${KIT_REPO:-https://github.com/avialtsin/session-runtime-kit.git}"
KIT="${KIT_DIR:-$HOME/session-runtime-kit}"
BIN="$HOME/.local/bin"
step() { printf '\n\033[1m— %s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(uname)" = Darwin ] || { echo "Установка рассчитана на macOS." >&2; exit 1; }
# git и python3 — из Command Line Tools Mac.
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install 2>/dev/null || true
  echo "Mac ставит Command Line Tools (git, python3). Дождись конца установки и запусти команду ещё раз." >&2
  exit 1
fi

mkdir -p "$BIN"
export PATH="$BIN:$PATH"
grep -qsF '.local/bin' "$HOME/.zshrc" || printf '\n# Команды агентов (artifacts, backlog)\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"

if ! have backlog; then
  step "Ставлю доску задач Backlog.md"
  arch="$( [ "$(uname -m)" = arm64 ] && echo arm64 || echo x64 )"
  # Сеть до GitHub иногда рвётся: повторы, и файл встаёт на место только целиком.
  tmp="$(mktemp)"
  curl -fL --retry 4 --retry-all-errors --retry-delay 2 --progress-bar -o "$tmp" \
    "https://github.com/MrLesk/Backlog.md/releases/latest/download/backlog-bun-darwin-$arch"
  chmod +x "$tmp" && mv "$tmp" "$BIN/backlog"
fi

if [ -d "$KIT/.git" ]; then
  step "Обновляю пакет в $KIT"
  git -C "$KIT" pull -q --ff-only || { sleep 3; git -C "$KIT" pull -q --ff-only; }
else
  step "Скачиваю пакет в $KIT"
  git clone -q "$REPO_URL" "$KIT" || { sleep 3; rm -rf "$KIT"; git clone -q "$REPO_URL" "$KIT"; }
fi

# Команды Orca для агента (вкладки, деревья, браузер): скилл orca-cli под версию своей Orca.
ORCA="$(command -v orca || true)"
[ -n "$ORCA" ] || ORCA=/Applications/Orca.app/Contents/Resources/bin/orca
if [ ! -e "$HOME/.agents/skills/orca-cli" ]; then
  step "Ставлю скилл orca-cli"
  if [ -x "$ORCA" ] && "$ORCA" skills get orca-cli > /tmp/orca-cli-skill.md 2>/dev/null; then
    mkdir -p "$HOME/.agents/skills/orca-cli" "$HOME/.claude/skills"
    mv /tmp/orca-cli-skill.md "$HOME/.agents/skills/orca-cli/SKILL.md"
    [ -e "$HOME/.claude/skills/orca-cli" ] || ln -s ../../.agents/skills/orca-cli "$HOME/.claude/skills/orca-cli"
  else
    echo "Orca не найдена — поставь Orca и запусти команду ещё раз."
  fi
fi

step "Ставлю правила, скиллы и табло"
python3 "$KIT/install.py"

step "Готово"
echo "Открой новую вкладку Orca, запусти codex в папке проекта и скажи «go help»."
