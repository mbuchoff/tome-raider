"""Scores each marked capture's latest run, and the run its marks were set on, against the marks (see DESIGN.md).
usage: python tools/eval.py [--local DIR]     (without --local: Azure when AZURE_STORAGE_CONNECTION_STRING is set, else ./var/store)"""
import argparse, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from shelfscan import evaluate
from shelfscan.store import LocalStore, open_store

ap = argparse.ArgumentParser()
ap.add_argument('--local', help='a local store folder')
a = ap.parse_args()
s = evaluate.summary(LocalStore(a.local) if a.local else open_store())
if not s['captures']: sys.exit('No capture has marks yet.')
print(f"{'capture':16} {'marks':>5}  {'run':18} {'baseline':26} {'agree':>5} {'disagree':>8} {'not found':>9} {'unanswered':>10}")
for c in s['captures']:
    if not c['runs']: print(f"{c['id']:16} {c['marks']:>5}  no finished run")
    for i, r in enumerate(c['runs']):
        print(f"{c['id'] if i == 0 else '':16} {c['marks'] if i == 0 else '':>5}  {', '.join(r['roles']):18} {r['baseline_label']:26} "
              f"{r['agree']:>5} {r['disagree']:>8} {r['not found']:>9} {r['unanswered']:>10}")
for name, key in (('latest runs', 'latest'), ('marked-on runs', 'marked_on')):
    t = s['totals'][key]
    print(f"{name}: {t['agree']} agree, {t['disagree']} disagree, {t['not found']} not found, {t['unanswered']} unanswered")
