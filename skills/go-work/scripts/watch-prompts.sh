#!/bin/bash
# Сторож подтверждений: раз в 20 с читает экраны вкладок Claude в рабочих деревьях Orca, путь которых
# содержит $1 (например, "workspaces/shop/shop-"), и выходит, когда одна из них ждёт ручного
# подтверждения. Печатает «PROMPT <дерево> <handle>» и хвост экрана. Нет вкладок — «NO_TABS».
# Запуск в фоне координатором: watch-prompts.sh <часть пути деревьев> [пауза в секундах]
filter="${1:?укажи часть пути рабочих деревьев}"; pause="${2:-20}"
while true; do
  handles=$(orca terminal list --json 2>/dev/null | FILTER="$filter" python3 -c "
import json,os,sys
d=json.loads(sys.stdin.read(),strict=False)['result']; ts=d if isinstance(d,list) else d.get('terminals',[])
for t in ts:
  wt=str(t.get('worktreeId') or '')
  if os.environ['FILTER'] in wt and t.get('agentIdentity')=='claude': print(t['handle'], wt.split('/')[-1])
")
  [ -z "$handles" ] && { echo "NO_TABS"; exit 0; }
  while read -r h name; do
    screen=$(orca terminal read --terminal "$h" --screen --json 2>/dev/null | python3 -c "
import json,sys
t=json.loads(sys.stdin.read(),strict=False)['result']['terminal'].get('tail') or []
print('\n'.join(l for l in t if l.strip()))
")
    if echo "$screen" | tail -25 | grep -qE "Do you want to|❯ 1\. Yes|Allow .*\?"; then
      echo "PROMPT $name $h"; echo "$screen" | tail -30; exit 0
    fi
  done <<< "$handles"
  sleep "$pause"
done
