"""Run a scan from the command line and write a static results page.

usage: ANTHROPIC_API_KEY=... python cli.py PHOTO [PHOTO...] --category rare --out DIR [--overrides "3=signed"] [--minimum 75]
       [--note "..."] [--no-second-read] [--cap 2.00] [--cache DIR]"""
import argparse, datetime, json, os, shutil
import jinja2
from shelfscan import pipeline
from shelfscan.claude import Claude
from shelfscan.rules import CATEGORIES

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
photos = []
for i, p in enumerate(a.photos, 1):
    name = f'upload{i}{os.path.splitext(p)[1].lower()}'
    shutil.copy(p, os.path.join(a.out, name))
    photos.append(dict(file=name, name=os.path.basename(p), category=a.category, overrides={str(k): v for k, v in pipeline.parse_overrides(a.overrides).items()},
                       minimum=a.minimum, note=a.note, second_read=not a.no_second_read))
job = dict(id='cli', created=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), status='queued', cap=a.cap, photos=photos)
pipeline.save_job(a.out, job)
job, results = pipeline.run(a.out, Claude(a.cache))

env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, 'templates')), autoescape=True)
env.globals.update(ordinal=lambda n: f"{int(n)}{'th' if 10 <= int(n) % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(int(n) % 10, 'th')}", categories=CATEGORIES)
html = env.get_template('job.html').render(job=job, results=results, can_rerun=False, file_url=lambda n: n)
open(os.path.join(a.out, 'report.html'), 'w').write(html)
print(json.dumps({'status': job['status'], 'error': job.get('error'), 'cost': results['cost'], 'seconds': results['seconds'],
                  'pulls': [[x['n'], x['label'], x['assessment']['value'], 1 + len(x['copies'])] for ph in results['photos'] for x in ph['pulls']],
                  'leads': [[x['n'], x['label'], 1 + len(x['copies'])] for ph in results['photos'] for x in ph.get('leads', [])]}, indent=1))
