#!/bin/bash
# Строка состояния Claude: прежняя строка машины (если была), под ней — табло сессии.
input=$(cat)
before="$HOME/.agents/session-runtime/statusline-before.sh"
[ -f "$before" ] && printf '%s' "$input" | bash "$before"
printf '%s' "$input" | python3 "$(dirname "$(readlink -f "$0")")/artifacts" statusline
