#!/usr/bin/env python3
"""Память всех проектов Claude Code для ретро: сводка и копия перед чисткой.

  list   — проекты (последняя сессия, жива ли папка, куда, похоже, переехал) и таблица всех заметок:
           проект, файл, тип, дата, размер, описание, похожие заметки и копии в других проектах;
           🔑 — в тексте похоже на ключ или пароль; отдельно — сломанные индексы MEMORY.md.
  dump   — полный текст заметок с теми же номерами, что в list, чтобы прочитать подряд; у 🔑 текст
           не показывается.
  backup — полная копия всех папок памяти в ~/.agents/session-runtime/backup/<дата>-memory/.

Запуск: python3 -I memory.py list [--since ГГГГ-ММ-ДД] --out memory.md
        python3 -I memory.py dump [--type feedback] [--project shop] [--since ГГГГ-ММ-ДД] --out notes.md
        python3 -I memory.py backup
"""
import argparse
import glob
import json
import os
import re
import shutil
from datetime import datetime

HOME = os.path.expanduser('~')
BASE = HOME + '/.claude/projects/'
SIMILAR = 0.34   # порог похожести описаний (доля общих основ слов)
SECRET = re.compile(
    r'ghp_\w{20,}|github_pat_\w{20,}|\bsk-[\w-]{20,}|AKIA[0-9A-Z]{16}|xox[bp]-[\w-]{10,}|AIza[\w-]{30,}'
    r'|\b\d{8,10}:[\w-]{30,}'                       # токен телеграм-бота
    r'|\b[0-9a-f]{32,}\b'                            # ключи API в hex
    r'|/v[23]/[\w-]{20,}|[?&](?:api_?key|key|token)=[\w-]{16,}'  # ключ в адресе RPC или API
    r'|(?i:password|passwd|пароль)\s*[:=]\s*\S{6,}')
NEAR_KEY = re.compile(r'(?i:key|token|ключ|secret|секрет|пароль|password)[^\n]{0,40}?([A-Za-z0-9_.-]{20,})')
MIXED = re.compile(r'(?<![\w-])(?=[\w-]*\d)(?=[\w-]*[A-Z])(?=[\w-]*[a-z])[A-Za-z0-9_-]{28,}(?![\w-])', re.A)


def has_secret(text):
    # длинная смесь регистров и цифр — похоже на токен; имена папок сессий, kebab-слова и адреса
    # кошельков (публичные) — нет
    if SECRET.search(text):
        return True
    # ключи 2ГИС и Яндекса — uuid; uuid есть и у сессий, поэтому только в строке про ключ
    uuid = r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b'
    if any(re.search(uuid, ln) and re.search(r'(?i)api.?key|\bkey\b|token|ключ', ln)
           for ln in text.splitlines()):
        return True
    def tokenish(w):
        return (re.search(r'\d', w) and not re.search(r'[a-z]{4}-|-[a-z]{4}|^\d{2,}-|^0x[0-9a-fA-F]{40}$|\.\w{2,4}$', w))
    return (any(tokenish(m.group(0)) for m in MIXED.finditer(text))
            or any(tokenish(m.group(1)) for m in NEAR_KEY.finditer(text)))


def project_of(mem_dir):
    # имя папки проекта — путь со слешами, заменёнными на дефисы; кириллица в нём теряется
    name = os.path.basename(os.path.dirname(mem_dir))
    name = name.replace('-Users-' + os.path.basename(HOME) + '-', '')
    return re.sub(r'-{3,}', '…', name) or name


def encode(path):
    # так Claude Code называет папку проекта: всё, кроме латиницы и цифр, — дефис
    return re.sub(r'[^A-Za-z0-9]', '-', path)


def known_dirs():
    # папки, где обычно живут проекты, — чтобы найти путь проекта без диалогов
    found = {}
    for root, depth in ((HOME, 1), (HOME + '/orca', 3), (HOME + '/projects', 3),
                        (HOME + '/Developer', 3)):
        for cur, dirs, _ in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'node_modules']
            if cur[len(root):].count('/') >= depth:
                dirs[:] = []
            found[encode(cur)] = cur
    return found


def moved_to(enc_name, dirs):
    # папки нет — ищем живую с тем же хвостом пути: old/shop-site → projects/shop/site
    best, hits = 0, []
    for enc, path in dirs.items():
        parts = path.rstrip('/').split('/')
        for n in (2, 1):
            tail = encode('/'.join(parts[-n:]))
            if len(tail) >= 6 and enc != enc_name and enc_name.endswith('-' + tail):
                if len(tail) > best:
                    best, hits = len(tail), [path]
                elif len(tail) == best and path not in hits:
                    hits.append(path)
                break
    return hits[:2]


def project_state(mem_dir, dirs):
    # где живёт проект и когда в нём последний раз работали — по транскриптам рядом с памятью
    sessions = glob.glob(os.path.join(os.path.dirname(mem_dir), '*.jsonl'))
    last = max((os.path.getmtime(f) for f in sessions), default=0)
    cwd = ''
    for f in sorted(sessions, key=os.path.getmtime, reverse=True)[:3]:
        for line in open(f, errors='ignore'):
            if '"cwd"' in line:
                try:
                    cwd = json.loads(line).get('cwd') or ''
                except ValueError:
                    continue
                if cwd:
                    break
        if cwd:
            break
    enc_name = os.path.basename(os.path.dirname(mem_dir))
    cwd = cwd or dirs.get(enc_name, '')
    alive = bool(cwd) and os.path.isdir(cwd)
    return {'cwd': cwd, 'alive': alive, 'moved': [] if alive else moved_to(enc_name, dirs),
            'last': datetime.fromtimestamp(last) if last else None}


def frontmatter(text):
    m = re.match(r'---\n(.*?)\n---\n?', text, re.S)
    head = m.group(1) if m else ''

    def field(key):
        f = re.search(rf'^\s*{key}:\s*"?(.*?)"?\s*$', head, re.M)
        return f.group(1) if f else ''
    return {'name': field('name'), 'description': field('description'), 'type': field('type'),
            'body': text[m.end():].strip() if m else text.strip()}


def stems(s):
    return {w[:5] for w in re.findall(r'[a-zа-яё]{4,}', s.lower())}


def collect():
    notes, broken, projects, dirs = [], [], {}, known_dirs()
    for mem_dir in sorted(glob.glob(BASE + '*/memory')):
        proj = project_of(mem_dir)
        projects[proj] = project_state(mem_dir, dirs)
        index_path = os.path.join(mem_dir, 'MEMORY.md')
        index = open(index_path, errors='ignore').read() if os.path.exists(index_path) else ''
        linked = set(re.findall(r'\]\(([^)]+\.md)\)', index))
        files = sorted(f for f in glob.glob(mem_dir + '/*.md') if not f.endswith('/MEMORY.md'))
        for f in files:
            text = open(f, errors='ignore').read()
            fm = frontmatter(text)
            notes.append({'project': proj, 'path': f, 'file': os.path.basename(f),
                          'mtime': datetime.fromtimestamp(os.path.getmtime(f)), **fm,
                          'secret': has_secret(fm['body']),
                          'stems': stems(fm['name'] + ' ' + fm['description'])})
            if os.path.basename(f) not in linked:
                broken.append((proj, os.path.basename(f), 'нет в MEMORY.md'))
        for link in sorted(linked):
            if not os.path.exists(os.path.join(mem_dir, link)):
                broken.append((proj, link, 'ссылка в MEMORY.md без файла'))
    return notes, broken, projects


def similar(notes):
    # похожие заметки в разных проектах — кандидаты в общее правило; «=» — тот же файл (копия при переезде)
    for i, a in enumerate(notes):
        a['similar'] = []
        for j, b in enumerate(notes):
            if i == j or a['project'] == b['project']:
                continue
            if a['file'] == b['file'] or (a['name'] and a['name'] == b['name']):
                a['similar'].insert(0, f"= {b['project']}/{b['file']}")
                continue
            if not a['stems'] or not b['stems']:
                continue
            score = len(a['stems'] & b['stems']) / min(len(a['stems']), len(b['stems']))
            if score >= SIMILAR:
                a['similar'].append(f"{b['project']}/{b['file']}")


def cell(s):
    return s.replace('|', '/').replace('\n', ' ')


def cmd_list(a):
    notes, broken, projects = collect()
    similar(notes)
    since = datetime.fromisoformat(a.since) if a.since else None
    out = [f"# Память проектов · {datetime.now():%d.%m.%Y %H:%M}", '',
           f"Заметок: {len(notes)}, проектов: {len({n['project'] for n in notes})}."
           + (f" Новых и изменённых с {a.since}: {sum(n['mtime'] >= since for n in notes)} (★)."
              if since else ''), '',
           '## Проекты', '', '| проект | заметок | последняя сессия | папка проекта |', '|---|---|---|---|']
    for proj, st in projects.items():
        count = sum(n['project'] == proj for n in notes)
        where = (st['cwd'].replace(HOME, '~') if st['alive'] else
                 f"папки нет: {st['cwd'].replace(HOME, '~')}" if st['cwd'] else
                 'папки нет — память больше не загружается')
        if st['moved']:
            where += ' · похоже, переехал → ' + ', '.join(m.replace(HOME, '~') for m in st['moved'])
        out.append(f"| {proj} | {count} | {st['last']:%d.%m.%y} | {cell(where)} |" if st['last']
                   else f"| {proj} | {count} | — | {cell(where)} |")
    out += ['', '## Заметки', '',
           '| # | проект | файл | тип | дата | знаков | описание | похожие в других проектах |',
           '|---|---|---|---|---|---|---|---|']
    for k, n in enumerate(notes, 1):
        mark = ('★ ' if since and n['mtime'] >= since else '') + ('🔑 ' if n['secret'] else '')
        out.append(f"| {k} | {n['project']} | {mark}{n['file']} | {n['type']} | "
                   f"{n['mtime']:%d.%m.%y} | {len(n['body'])} | {cell(n['description'])[:220]} | "
                   f"{cell(', '.join(n['similar'][:4]))} |")
    if broken:
        out += ['', '## Сломанные индексы MEMORY.md', '', '| проект | файл | что |', '|---|---|---|']
        out += [f'| {p} | {f} | {w} |' for p, f, w in broken]
    open(a.out, 'w').write('\n'.join(out) + '\n')
    print(f"{len(notes)} заметок, похожих пар в разных проектах: "
          f"{sum(len(n['similar']) for n in notes) // 2}, сломанных ссылок индекса: {len(broken)}, "
          f"проектов без папки: {sum(not s['alive'] for s in projects.values())}, "
          f"заметок с 🔑: {sum(n['secret'] for n in notes)}")


def cmd_dump(a):
    notes, _, projects = collect()
    since = datetime.fromisoformat(a.since) if a.since else None
    out, count = [], 0
    for k, n in enumerate(notes, 1):
        if (a.type and not re.search(rf'\b{a.type}\b', n['type'])) or (a.project and a.project not in n['project']) \
                or (since and n['mtime'] < since):
            continue
        count += 1
        st = projects[n['project']]
        out += [f"## {k} · {n['project']}{'' if st['alive'] else ' (папки нет)'} · {n['file']} · "
                f"{n['type']} · {n['mtime']:%d.%m.%y}", f"_{n['description']}_", '',
                '[🔑 похоже на ключ или пароль — текст не показан, открыть вручную]' if n['secret']
                else n['body'], '']
    text = '\n'.join(out) + '\n'
    open(a.out, 'w').write(text)
    print(f"заметок: {count}, строк: {text.count(chr(10))}")


def cmd_backup(_):
    dst = os.path.join(HOME, '.agents/session-runtime/backup', f"{datetime.now():%y%m%d-%H%M%S}-memory")
    count = 0
    for mem_dir in glob.glob(BASE + '*/memory'):
        shutil.copytree(mem_dir, os.path.join(dst, os.path.basename(os.path.dirname(mem_dir))))
        count += 1
    print(f"скопировано папок памяти: {count} → {dst}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('list')
    p.add_argument('--since')
    p.add_argument('--out', required=True)
    p = sub.add_parser('dump')
    p.add_argument('--type')
    p.add_argument('--project')
    p.add_argument('--since')
    p.add_argument('--out', required=True)
    sub.add_parser('backup')
    a = ap.parse_args()
    {'list': cmd_list, 'dump': cmd_dump, 'backup': cmd_backup}[a.cmd](a)


if __name__ == '__main__':
    main()
