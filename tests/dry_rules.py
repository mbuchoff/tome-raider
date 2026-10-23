"""Runs the rules on saved spine readings (the API test's TSVs), with no API calls.
usage: python tests/dry_rules.py <inventory.tsv> <category> [more pairs...]"""
import csv, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shelfscan.refdata import Reference
from shelfscan import rules

ref = Reference()
args = sys.argv[1:]
for path, cat in zip(args[::2], args[1::2]):
    bs = []
    for r in csv.DictReader(open(path, newline=''), delimiter='\t'):
        r.update(shelf=int(r['shelf']), pos=int(r['pos']), of=0, confidence=float(r['confidence'] or 0.5), x0=0, x1=0, y0=0, y1=0, appearance='')
        bs.append(r)
    pulls, leads, np_, skipped = rules.evaluate(bs, lambda s: cat, ref, lambda c: ref.rules['minimum_value'][c])
    print(f'\n== {os.path.basename(path)} as {cat}: {len(bs)} books, {len(pulls)} pulls, {len(leads)} leads, {len(np_)} not pulled, skipped {skipped}')
    for p in pulls: print('  PULL', p['label'][:48], '|', round(p['assessment']['value'] or 0), '|', p['assessment']['why'][:100])
    for p in leads: print('  LEAD', p['label'][:48], '|', p['assessment']['why'][:100])
    for p in np_: print('  not ', p['label'][:48], '|', p['assessment']['reason'][:60], '|', '; '.join(p['assessment']['signals'])[:70])
