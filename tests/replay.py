"""Re-runs the current rules on the readings saved in one or more results.json files, with no API calls.
usage: python tests/replay.py results.json [results.json...] [--category rare] [--quiet]"""
import argparse, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shelfscan.refdata import Reference
from shelfscan import rules

ap = argparse.ArgumentParser()
ap.add_argument('results', nargs='+')
ap.add_argument('--category', help='use this category for every shelf instead of the recorded ones')
ap.add_argument('--quiet', action='store_true', help='counts only')
a = ap.parse_args()
ref = Reference()
for path in a.results:
    for ph in json.load(open(path))['photos']:
        cats = {s['shelf']: (a.category or s['category'], s['minimum']) for s in ph['shelves']}
        def shelf_category(s): return cats.get(s, (a.category or ph['category'], None))[0]
        def minimum_for(c): return ref.rules['minimum_value'][c]
        pulls, leads, not_pulled, skipped = rules.evaluate(ph['readings'], shelf_category, ref, minimum_for)
        print(f"\n== {path.split('/')[-2]}/{ph['name']}: {len(ph['readings'])} books ({sum(map(rules.unreadable, ph['readings']))} unreadable), {len(pulls)} pulls (was {len(ph['pulls'])}), "
              f"{len(leads)} leads (was {len(ph.get('leads', []))}), {len(not_pulled)} not pulled, skipped {skipped}")
        if a.quiet: continue
        for kind, xs in (('PULL', pulls), ('LEAD', leads)):
            for x in xs:
                value = rules.fmt_money(x['assessment']['value']).rjust(14)
                n = f"x{1 + len(x['copies'])}" if x['copies'] else '  '
                print(f"  {kind} s{x['book']['shelf']} {n} {x['label'][:46]:46} | {value} | {x['assessment']['why'][:90]}")
        for x in not_pulled:
            print(f"  not  s{x['book']['shelf']}    {x['label'][:46]:46} | {x['assessment']['reason'][:70]}")
