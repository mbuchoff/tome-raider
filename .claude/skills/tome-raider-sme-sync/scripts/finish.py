"""Records a finished sync in state.json, so the next sync reports only what's new: the time the run's data was pulled,
the page version published, the score, his answers and ticks as they stood, and every mark's result. Keeps a copy of the
published page in the run folder. Run it only after the page is published (or with --no-publish when nothing changed).
usage: python finish.py RUN_DIR (--version N | --no-publish) [--emailed]"""
import argparse, glob, os, shutil
from common import load_json, path, save_json

ap = argparse.ArgumentParser()
ap.add_argument('run_dir')
g = ap.add_mutually_exclusive_group(required=True)
g.add_argument('--version', type=int); g.add_argument('--no-publish', action='store_true')
ap.add_argument('--emailed', action='store_true')
a = ap.parse_args()
app, rep = load_json(os.path.join(a.run_dir, 'app.json')), load_json(os.path.join(a.run_dir, 'replay.json'))
state = load_json(path('state.json'), {})
state['last_sync'] = app['pulled']
if a.version: state['page_version'] = a.version
state['score'] = rep['score']
state['code'] = rep['code']
state['db'] = {c: {os.path.basename(p)[:-5]: load_json(p) for p in glob.glob(os.path.join(a.run_dir, 'db', c, '*.json'))} for c in ('answers', 'acks')}
state['marks'] = {r['id']: dict(outcome=r['outcome'], status=r['status'], value=r['value']) for r in rep['rows']}
state.setdefault('history', []).append(dict(run=os.path.basename(a.run_dir), pulled=app['pulled'], score=rep['score'],
                                            page_version=a.version, emailed=a.emailed))
save_json(path('state.json'), state)
if a.version and os.path.exists(path('page.html')): shutil.copy(path('page.html'), os.path.join(a.run_dir, f'page.v{a.version}.html'))
print(f"state.json: last sync {app['pulled']}, page version {state.get('page_version')}, "
      f"{rep['score']['agree']} of {rep['score']['total']} agree{', email sent' if a.emailed else ''}")
