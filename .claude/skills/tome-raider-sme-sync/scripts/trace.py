"""Why the rules decided what they did for some marked books: the reading, the foot close-up, the notes, the signals and
the sales used, from the run folder's replay.json. Use it before explaining a book on the page.
usage: python trace.py RUN_DIR [title words ...]   (no words: every book that disagrees with its mark)"""
import json, os, sys
from common import load_json, money

run_dir, words = sys.argv[1], [w.lower() for w in sys.argv[2:]]
for r in load_json(os.path.join(run_dir, 'replay.json'))['rows']:
    if words and not any(w in r['label'].lower() for w in words): continue
    if not words and r['outcome'] != 'disagree': continue
    print('=' * 100)
    print(f"{r['label']} | marked {r['verdict']} | now {r['status']} {money(r['value'])} | {r['outcome']} | capture {r['capture']}, marked {r['marked_at']}")
    if r['note']: print(f"  his note: {json.dumps(r['note'], ensure_ascii=False)}")
    print('  read:', {k: v for k, v in r['reading'].items() if v not in (None, '')})
    if r.get('foot'): print('  foot:', {k: v for k, v in r['foot'].items() if v})
    if r.get('notes'): print('  notes:', {k: v for k, v in r['notes'].items() if v})
    print(f"  basis: {r['basis'] or '-'} | reason: {r['reason'] or '-'}")
    print('  signals:', r['signals'])
    if r['checks']: print('  checks:', r['checks'])
    for c in r['comps']: print(f"   sale {money(c['price'])} {c['year'] or ''} {c['file'] or ''} | {c['title']}")
