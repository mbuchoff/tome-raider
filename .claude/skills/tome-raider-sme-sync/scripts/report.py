"""What's new since the last sync, from the run folder (app.json, replay.json, db/answers, db/acks) against state.json and
page.json: his answers, notes and ticks on the page; new photos and marks in the app; marks whose outcome changed; every
current disagreement and whether the page covers it; and page items ready to act on or remove. Writes report.md and
prints it. Text he wrote is quoted as data.
usage: python report.py RUN_DIR"""
import glob, json, os, sys
from common import load_json, money, path, score
from render import matches

run_dir = sys.argv[1]
app, rep = load_json(os.path.join(run_dir, 'app.json')), load_json(os.path.join(run_dir, 'replay.json'))
state, page = load_json(path('state.json'), {}), load_json(path('page.json'))
since = state.get('last_sync', '')
rows = rep['rows']
db = {c: {os.path.basename(p)[:-5]: load_json(p) for p in glob.glob(os.path.join(run_dir, 'db', c, '*.json'))} for c in ('answers', 'acks')}
if not any(db.values()): sys.exit('no db/answers or db/acks in the run folder: save them with ArtifactData list (out_dir=RUN_DIR/db) first')
old = state.get('db', {'answers': {}, 'acks': {}})
q = lambda s: json.dumps(s, ensure_ascii=False)
out = []
w = out.append

s, ps = score(rows), state.get('score') or {}
w(f'# Sync report {os.path.basename(run_dir)}\n')
w(f'Last sync: {since or "never"}. Code replayed: {rep["code"][:12]}; newest app run used {app["newest_run_baseline"][:12] or "?"}; '
  f'poc HEAD {app["poc_head"][:12]}{" with uncommitted changes" if app["poc_dirty"] else ""}.'
  + ('' if app['newest_run_baseline'][:12] in ('', rep['code'][:12]) else ' **The replayed code differs from what the newest run used: check what is deployed.**') + '\n')
w(f'**Score:** {s["agree"]} of {s["total"]} agree, {s["disagree"]} disagree, {s["not found"]} not found'
  + (f' (last sync: {ps.get("agree")} of {ps.get("total")}, {ps.get("disagree")} disagree)' if ps else '') + '\n')

items = {i['id']: i for i in page['items'] if i.get('status', 'open') != 'removed'}
on_page = set(items) | {page['disagreements']['id'], page['general']['id']}

w('## His input on the page\n')
news = 0
for kind in ('answers', 'acks'):
    for k, d in sorted(db[kind].items()):
        o = old.get(kind, {}).get(k)
        if o and o.get('at') == d.get('at'): continue
        news += 1
        what = (f'choice {q(d.get("choice"))}' if kind == 'answers' else f'ticked done: {d.get("done")}') + (f', note {q(d["note"])}' if d.get('note') else '')
        w(f'- **{kind[:-1]} {k}**{"" if k in on_page else " (not on the page now)"}: {what} ({d.get("at")}{", new" if not o else ", changed"})')
if not news: w('- Nothing new.')
w('')

w('## Page items to act on\n')
acts = 0
for k, it in items.items():
    a, n = db['acks'].get(k) or {}, db['answers'].get(k) or {}
    if a.get('done'): w(f'- **{k}** ({it["kind"]}): ticked done{" with note " + q(a["note"]) if a.get("note") else ""} -> remove (page.py remove)'); acts += 1
    elif a.get('note'): w(f'- **{k}** ({it["kind"]}): not ticked, note {q(a["note"])} -> look into it'); acts += 1
    if n.get('choice') and it['kind'] == 'call': w(f'- **{k}** (call): answered {q(n["choice"])} -> act on it, then make it a Done item'); acts += 1
    elif n.get('note') and not (old.get('answers', {}).get(k) or {}).get('at') == n.get('at'): w(f'- **{k}** ({it["kind"]}): note {q(n["note"])} -> act on it'); acts += 1
def waiting_on_him(k, it):          # a call waits for his choice, a done item with a tick box for his tick, the rest for a note
    a, n = db['acks'].get(k) or {}, db['answers'].get(k) or {}
    if it['kind'] == 'call': return not n.get('choice')
    if it['kind'] == 'done' and it.get('ack', True): return not a.get('done')
    return not n.get('note')
waiting = [k for k, it in items.items() if waiting_on_him(k, it)]
if not acts: w('- Nothing to act on.')
w(f'- Waiting on him: {", ".join(waiting) or "nothing"}\n')

w('## New in the app\n')
caps = [c for c in app['captures'] if (c.get('created') or '') > since]
w(f'{len(caps)} new photo captures since the last sync' + (': ' + ', '.join(f'{c["id"]} ({(c["created"] or "")[:10]}, {c["photos"]} photo{"s" if c["photos"] != 1 else ""}'
                                                                  f'{", run $" + format(c["latest_run"]["spent"], ".2f") if c.get("latest_run") and c["latest_run"].get("spent") is not None else ""})' for c in caps) if caps else '') + '.\n')
new = [r for r in rows if (r.get('marked_at') or '') > since]
w(f'**{len(new)} new marks** ({sum(r["outcome"] == "agree" for r in new)} agree, {sum(r["outcome"] == "disagree" for r in new)} disagree):\n')
for r in sorted(new, key=lambda r: (r['outcome'] != 'disagree', r['capture'], r['label'])):
    w(f'- {r["outcome"].upper()} {r["label"]} [{r["capture"]}]: marked {r["verdict"]}, now {r["status"]} {money(r["value"])}' + (f' | note {q(r["note"])}' if r['note'] else ''))
w('')

prev = state.get('marks', {})
changed = [(r, prev[r['id']]) for r in rows if r['id'] in prev and (prev[r['id']]['outcome'], prev[r['id']]['status'], prev[r['id']]['value']) != (r['outcome'], r['status'], r['value'])]
w(f'## Older marks whose result changed ({len(changed)})\n')
for r, p in changed: w(f'- {r["label"]} [{r["capture"]}]: {p["status"]} {money(p["value"])} {p["outcome"]} -> {r["status"]} {money(r["value"])} {r["outcome"]}')
if not changed: w('- None.')
w('')

dis = [r for r in rows if r['outcome'] == 'disagree']
w(f'## Disagreements now ({len(dis)}) and the page\n')
entries = [(g['heading'], e) for g in page['disagreements']['groups'] for e in g['entries']]
for r in dis:
    at = [h for h, e in entries if not e.get('any') and any(matches([r], b) for b in e['books'])]
    w(f'- {r["label"]} [{r["capture"]}]: {r["status"]} {money(r["value"])}, marked {r["verdict"]} -> ' + (f'on the page under "{at[0]}"' if at else '**not on the page**'))
stale = [e['books'] for h, e in entries if not all(matches(rows if e.get('any') else dis, b) for b in e['books'])]
if stale: w('\nStale entries (they no longer match a disagreement; update or remove them): ' + '; '.join(map(str, stale)))
w('')
info = [r for r in new if r['outcome'] == 'agree' and r['note']]
if info:
    w('## Agreeing marks with notes (check what each says)\n')
    for r in info: w(f'- {r["label"]} [{r["capture"]}]: now {r["status"]} {money(r["value"])} | note {q(r["note"])}')
text = '\n'.join(out) + '\n'
open(os.path.join(run_dir, 'report.md'), 'w', encoding='utf-8').write(text)
print(text)
