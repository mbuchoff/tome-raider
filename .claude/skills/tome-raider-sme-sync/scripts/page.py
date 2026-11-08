"""Lists, shows or removes items in page.json. A removed item moves to archive.json with the date, the reason, and his
answer and tick from the run folder's db copy, so nothing he wrote is lost (the page's database keeps them too).
usage: python page.py list
       python page.py show ID
       python page.py remove ID --why "ticked done 2 Nov" --run RUN_DIR"""
import argparse, json, os, sys
from common import load_json, now_iso, path, save_json

ap = argparse.ArgumentParser()
ap.add_argument('cmd', choices=['list', 'show', 'remove'])
ap.add_argument('id', nargs='?')
ap.add_argument('--why', default='')
ap.add_argument('--run')
a = ap.parse_args()
page = load_json(path('page.json'))

if a.cmd == 'list':
    for it in page['items']:
        print(f"{it['id']:24} {it['kind']:9} {it.get('done_on') or it.get('added', ''):11} {it.get('topic', '')}")
    print(f"{page['disagreements']['id']:24} {'list':9} {'':11} {sum(len(g['entries']) for g in page['disagreements']['groups'])} entries")
    sys.exit()
it = next((i for i in page['items'] if i['id'] == a.id), None)
if not it: sys.exit(f'no item {a.id!r} on the page')
if a.cmd == 'show':
    print(json.dumps(it, indent=1, ensure_ascii=False)); sys.exit()
if not a.why or not a.run: sys.exit('remove needs --why and --run')
db = {c: load_json(os.path.join(a.run, 'db', c, a.id + '.json'), {}) for c in ('answers', 'acks')}
arch = load_json(path('archive.json'), [])
arch.append(dict(removed=now_iso(), why=a.why, answer=db['answers'] or None, ack=db['acks'] or None, item=it))
save_json(path('archive.json'), arch)
page['items'] = [i for i in page['items'] if i['id'] != a.id]
save_json(path('page.json'), page)
print(f'removed {a.id} ({it["kind"]}) to archive.json')
