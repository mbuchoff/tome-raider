"""Run a scan from the command line and write a static results page.

The photos become a capture with one run in a folder store under --out (out/store), as the app would keep them; results.json
and report.html are also written straight into --out.

usage: ANTHROPIC_API_KEY=... python cli.py PHOTO [PHOTO...] --category rare --out DIR [--overrides "3=signed"] [--minimum 75]
       [--note "..."] [--no-second-read] [--cap 2.00] [--cache DIR]"""
import argparse, json, os
import jinja2
from shelfscan import pipeline, repo, version
from shelfscan.claude import Claude
from shelfscan.rules import CATEGORIES
from shelfscan.store import LocalStore

HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
ap.add_argument('photos', nargs='+')
ap.add_argument('--category', required=True, choices=sorted(CATEGORIES))
ap.add_argument('--out', required=True)
ap.add_argument('--overrides', default='')
ap.add_argument('--minimum', type=float)
ap.add_argument('--note', default='')
ap.add_argument('--no-second-read', action='store_true')
ap.add_argument('--cap', type=float, default=2.00)
ap.add_argument('--cache', default=os.path.join(HERE, 'var', 'cache'))
a = ap.parse_args()

os.makedirs(a.out, exist_ok=True)
store = LocalStore(os.path.join(a.out, 'store'))
settings = dict(category=a.category, overrides={str(k): v for k, v in pipeline.parse_overrides(a.overrides).items()},
                minimum=a.minimum, note=a.note, second_read=not a.no_second_read)
capture = repo.create_capture(store, [(os.path.basename(p), open(p, 'rb').read()) for p in a.photos], settings)
cid = capture['id']
run = repo.start_run(store, cid, a.cap)
run = pipeline.run_stored(store, cid, run['id'], Claude(a.cache), os.path.join(a.out, 'work'))
results = repo.load_results(store, cid, run['id']) or {'photos': [], 'cost': run.get('cost'), 'seconds': None}
json.dump(results, open(os.path.join(a.out, 'results.json'), 'w'), indent=1, default=str)

env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, 'templates')), autoescape=True)
env.globals.update(ordinal=lambda n: f"{int(n)}{'th' if 10 <= int(n) % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(int(n) % 10, 'th')}", categories=CATEGORIES)
files = os.path.relpath(os.path.join(store.root, *repo.run_file_key(cid, run['id'], '').split('/')), a.out)
html = env.get_template('capture.html').render(capture=capture, runs=[run], run=run, results=results if results['photos'] else None, busy=False,
                                               baseline=version.baseline(), marks={}, file_url=lambda n: f'{files}/{n}')
open(os.path.join(a.out, 'report.html'), 'w').write(html)
print(json.dumps({'status': run['status'], 'error': run.get('error'), 'cost': results.get('cost'), 'seconds': results.get('seconds'),
                  'capture': cid, 'run': run['id'], 'baseline': (run.get('baseline') or {}).get('sha'),
                  'pulls': [[x['n'], x['label'], x['assessment']['value'], 1 + len(x['copies'])] for ph in results['photos'] for x in ph['pulls']],
                  'leads': [[x['n'], x['label'], 1 + len(x['copies'])] for ph in results['photos'] for x in ph.get('leads', [])]}, indent=1))
