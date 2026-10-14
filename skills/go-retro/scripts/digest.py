#!/usr/bin/env python3
"""Выжимка диалогов Claude Code и Codex для ретро.

Для каждой сессии, где за период писал пользователь, — файл markdown: реплики пользователя целиком
и последний текст агента перед каждой (на что пользователь реагирует; помечено, если агент отвечал на
письмо вкладки или фоновую задачу), реплики, присланные посреди хода агента, прерывания, кадры, автосжатия, задания вкладкам, сколько
инструментов агент вызвал между репликами. Субагенты и служебные записи пропускаются, сообщения
других вкладок только считаются. Продолжение сессии в новом файле повторяет старые реплики — повторы
выкидываются. Всё вместе по времени — all.md.

Запуск: python3 -I digest.py --since 2026-10-03 [--until "2026-10-10 21:30"] [--skip <часть имени проекта>] --out <папка>
"""
import argparse
import glob
import json
import os
import re
from datetime import datetime

HOME = os.path.expanduser('~')
AGENT_HEAD = 450     # ответ агента: начало…
AGENT_TAIL = 250     # …и конец — там обычно итог и вопрос пользователю
USER_CUT = 2500      # длинные вставки пользователя режем
TASK_CUT = 500       # задание вкладке писал агент — хватит начала


def text_of(content):
    if isinstance(content, str):
        return content, 0
    parts, images = [], 0
    for x in content or []:
        if x.get('type') == 'text':
            parts.append(x.get('text', ''))
        elif x.get('type') == 'image':
            images += 1
    return '\n'.join(parts), images


def cut(s, n):
    s = s.strip()
    return s if len(s) <= n else s[:n] + f' …[+{len(s) - n}]'


def cut_mid(s, head, tail):
    s = s.strip()
    return s if len(s) <= head + tail + 40 else f'{s[:head]} …[{len(s) - head - tail}]… {s[-tail:]}'


def clean_user(s):
    # вставки хуков и системы попадают в текст реплики
    return re.sub(r'<system-reminder>.*?</system-reminder>', '', s, flags=re.S).strip()


def is_tab_task(s):
    # первое сообщение вкладки пишет запустивший её агент (скилл project; раньше new-tab, parallel-tabs):
    # «Ты — …», «Ты ведёшь…», «Задание…» или преамбула оркестрации Orca. Голосом пользователь не
    # диктует путей, `кода` и номеров задач — по ним задание и отличается от реплики.
    head = s.lstrip()[:300]
    if head.startswith('Please carry out this task') or 'dispatched worker' in head:
        return True
    return head.startswith(('Задание', 'Ты ')) and any(m in s for m in ('`', '~/', 'TASK-')) or (
        len(s) > 3000 and ('ТВОЯ ОЧЕРЕДЬ' in s or 'оординатор' in s))


def project_name(cwd):
    cwd = cwd or ''
    for pre in (HOME + '/orca/workspaces/', HOME + '/'):
        if cwd.startswith(pre):
            return cwd[len(pre):] or '~'
    return cwd


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone() if ts else None


def claude_session(path):
    title, cwd, rows = '', '', []
    last_agent, tools, peers = '', 0, 0
    # на что отвечал агент: реплика пользователя, письмо вкладки (peer), фоновая задача (bg)
    trigger, agent_trigger = 'user', 'user'
    for line in open(path, errors='ignore'):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get('type')
        if t == 'ai-title':
            title = d.get('aiTitle') or d.get('title') or title
            continue
        if t == 'attachment' and (d.get('attachment') or {}).get('type') == 'queued_command':
            # прислано, пока агент работал, и агент «впитал» это посреди хода (absorbed_mid_turn):
            # отдельной записью, а не репликой user. Так приходит до трети реплик, среди них самые резкие.
            a = d['attachment']
            origin = (a.get('origin') or {}).get('kind')
            if origin == 'peer':
                peers += 1
                trigger = 'peer'
            elif a.get('commandMode') == 'task-notification' or origin == 'task-notification':
                trigger = 'bg'
            elif origin in ('human', None) and a.get('commandMode') == 'prompt':
                txt, images = text_of(a.get('prompt'))
                txt = clean_user(txt)
                if txt or images:
                    rows.append({'ts': parse_ts(d.get('timestamp')), 'kind': 'user', 'agent': last_agent,
                                 'agent_trigger': agent_trigger, 'tools': tools, 'user': txt,
                                 'images': images, 'queued': True, 'absorbed': True})
                    last_agent, tools, trigger = '', 0, 'user'
            continue
        if t not in ('user', 'assistant'):
            continue
        cwd = d.get('cwd') or cwd
        content = (d.get('message') or {}).get('content')
        if t == 'assistant':
            for x in content if isinstance(content, list) else []:
                if x.get('type') == 'tool_use':
                    tools += 1
                elif x.get('type') == 'text' and x.get('text', '').strip():
                    last_agent, agent_trigger = x['text'], trigger
            continue
        if isinstance(content, list) and any(x.get('type') == 'tool_result' for x in content):
            continue
        origin = (d.get('origin') or {}).get('kind') or d.get('turnOrigin')
        txt, images = text_of(content)
        ts = parse_ts(d.get('timestamp'))
        # автосжатие и прерывание приходят с любым origin, в том числе без него
        if txt.startswith('This session is being continued'):
            rows.append({'ts': ts, 'kind': 'compact'})
            continue
        if txt.startswith('[Request interrupted'):
            rows.append({'ts': ts, 'kind': 'interrupt'})
            continue
        if origin == 'peer':
            peers += 1
            trigger = 'peer'
            continue
        if origin in ('task-notification', 'task_notification', 'system', 'auto-continuation'):
            trigger = 'bg'
            continue
        if origin != 'human' or d.get('isMeta'):
            continue
        txt = clean_user(txt)
        if not txt and not images:
            continue
        rows.append({'ts': ts, 'kind': 'user', 'agent': last_agent, 'agent_trigger': agent_trigger,
                     'tools': tools, 'user': txt, 'images': images,
                     'queued': d.get('promptSource') == 'queued'})
        last_agent, tools, trigger = '', 0, 'user'
    return {'title': title, 'cwd': cwd, 'rows': rows, 'peers': peers}


def codex_session(path):
    cwd, rows, last_agent, tools = '', [], '', 0
    for line in open(path, errors='ignore'):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        p = d.get('payload') or {}
        if d.get('type') == 'session_meta':
            cwd = p.get('cwd', cwd)
        if d.get('type') != 'response_item':
            continue
        if p.get('type') in ('function_call', 'local_shell_call', 'custom_tool_call'):
            tools += 1
        if p.get('type') != 'message':
            continue
        txt = '\n'.join(c.get('text', '') for c in p.get('content') or [] if 'text' in c)
        if p.get('role') == 'assistant':
            last_agent = txt or last_agent
        elif p.get('role') == 'user':
            if txt.lstrip().startswith(('<environment_context', '<user_instructions', '# AGENTS.md')):
                continue
            rows.append({'ts': parse_ts(d.get('timestamp')), 'kind': 'user', 'agent': last_agent,
                         'agent_trigger': 'user', 'tools': tools, 'user': txt.strip(), 'images': 0,
                         'queued': False})
            last_agent, tools = '', 0
    return {'title': '', 'cwd': cwd, 'rows': rows, 'peers': 0}


def local(dt):
    return dt.strftime('%d.%m %H:%M') if dt else '?'


TRIGGER = {'peer': ' (отвечал на письмо вкладки)', 'bg': ' (отвечал на фоновую задачу)', 'user': ''}


def users(rows):
    return [r for r in rows if r['kind'] == 'user']


def render(s, kind, path):
    rows = s['rows']
    out = [f"# {project_name(s['cwd'])} · {s['title'] or '(без названия)'}",
           f"{kind} · {local(rows[0]['ts'])} → {local(rows[-1]['ts'])} · реплик {len(users(rows))}"
           f" · прерываний {sum(r['kind'] == 'interrupt' for r in rows)}"
           f" · сообщений вкладок {s['peers']}",
           f"`{path.replace(HOME, '~')}`"]
    if s.get('repeated'):
        out.append(f"Продолжение сессии из другого файла: {s['repeated']} повторённых реплик пропущено.")
    out.append('')
    prev = None
    for r in rows:
        if r['kind'] == 'compact':
            out += [f"**{local(r['ts'])} ⟲ автосжатие контекста**", '']
            continue
        if r['kind'] == 'interrupt':
            out += [f"**{local(r['ts'])} ✋ прерывание**", '']
            continue
        if prev is None and is_tab_task(r['user']):
            out += [f"**{local(r['ts'])} 📋 задание вкладке (писал агент):** {cut(r['user'], TASK_CUT)}", '']
            prev = r
            continue
        if r['agent']:
            out.append(f"> **агент{TRIGGER[r['agent_trigger']]}:** "
                       f"{cut_mid(r['agent'], AGENT_HEAD, AGENT_TAIL)}".replace('\n', '\n> '))
        meta = [f"инструментов {r['tools']}"] if r['tools'] else []
        if r['images']:
            meta.append(f"кадров {r['images']}")
        if r.get('absorbed'):
            meta.append('прислано, пока агент работал; агент прочёл посреди хода')
        elif r['queued']:
            meta.append('в очередь, пока агент работал')
        if prev and prev['user'] == r['user']:
            meta.append('та же реплика ещё раз')
        prev = r
        out += [f"**{local(r['ts'])} 🗣** {cut(r['user'], USER_CUT)}"
                + (f"  _({', '.join(meta)})_" if meta else ''), '']
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', required=True, help='ГГГГ-ММ-ДД[ ЧЧ:ММ], местное время')
    ap.add_argument('--until', help='ГГГГ-ММ-ДД[ ЧЧ:ММ], по умолчанию — сейчас')
    ap.add_argument('--skip', nargs='*', default=[],
                    help='пропустить проекты, в имени которых есть эта строка (личное)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since).astimezone()
    until = datetime.fromisoformat(a.until).astimezone() if a.until else datetime.now().astimezone()
    os.makedirs(a.out, exist_ok=True)
    items, skipped = [], 0
    paths = [('claude', f, claude_session) for f in glob.glob(HOME + '/.claude/projects/*/*.jsonl')]
    paths += [('codex', f, codex_session)
              for f in glob.glob(HOME + '/.codex/sessions/**/*.jsonl', recursive=True)]
    for kind, f, parse in paths:
        if os.path.getmtime(f) < since.timestamp():
            continue
        s = parse(f)
        if any(x in project_name(s['cwd']) for x in a.skip):
            skipped += 1
            continue
        s['rows'] = [r for r in s['rows'] if r['ts'] and since <= r['ts'] <= until]
        if users(s['rows']):
            items.append((kind, f, s))
    items.sort(key=lambda x: x[2]['rows'][0]['ts'])
    # продолжение сессии (--resume, автосжатие в новый файл) повторяет старые реплики — оставить первые
    seen, kept = set(), []
    for kind, f, s in items:
        keys = [(r['ts'], r.get('user', r['kind'])[:300]) for r in s['rows']]
        s['repeated'] = sum(k in seen and r['kind'] == 'user' for k, r in zip(keys, s['rows']))
        s['rows'] = [r for k, r in zip(keys, s['rows']) if k not in seen]
        seen.update(keys)
        if users(s['rows']):
            kept.append((kind, f, s))
    index, everything = [], []
    for kind, f, s in kept:
        first = s['rows'][0]['ts']
        proj = re.sub(r'[^\w-]+', '-', project_name(s['cwd']))[:40]  # вне f-строки: Python 3.9
        name = f"{first.strftime('%d%m-%H%M')}_{proj}_{os.path.basename(f)[:8]}.md"
        text = render(s, kind, f)
        open(os.path.join(a.out, name), 'w').write(text)
        everything.append(text)
        index.append((local(first), project_name(s['cwd']), s['title'], len(users(s['rows'])),
                      sum(len(r['user']) for r in users(s['rows'])), name))
    with open(os.path.join(a.out, '_index.md'), 'w') as fh:
        fh.write('| начало | проект | тема | реплик | символов | файл |\n|---|---|---|---|---|---|\n')
        for row in index:
            fh.write('| ' + ' | '.join(str(x) for x in row) + ' |\n')
    whole = '\n\n=====\n\n'.join(everything) + '\n'
    open(os.path.join(a.out, 'all.md'), 'w').write(whole)
    print(f"{len(index)} сессий, {sum(r[3] for r in index)} реплик, {whole.count(chr(10))} строк в all.md"
          + (f", пропущено сессий по --skip: {skipped}" if skipped else ''))


if __name__ == '__main__':
    main()
