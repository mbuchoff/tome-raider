"""Checks that like captures are consolidated: a repeat upload of the same photos becomes a new run of the same capture, and
tools/consolidate.py merges captures that share their photos. Folder store and a fake pipeline: no Claude calls.
usage: python tests/consolidate_check.py"""
import copy, glob, json, os, sys, tempfile, time
HOME = tempfile.mkdtemp()
os.environ.update(SHELFSCAN_HOME=HOME, SHELFSCAN_PASSCODE='x', SHELFSCAN_INSECURE_COOKIE='1', ANTHROPIC_API_KEY='unused')
for k in ('AZURE_STORAGE_CONNECTION_STRING', 'ANTHROPIC_ADMIN_KEY'): os.environ.pop(k, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as web
from shelfscan import index, marks, pipeline, repo
from tools import consolidate

SAMPLE = json.load(open(sorted(glob.glob('/tmp/claude-1000/-home-node-repos-norris/079c046b-3c36-4ac5-bc43-0762eabab3cb/scratchpad/fbcheck/r4-4ed2dbeb691f.json'))[0]))


def fake_run(job_dir, claude, now=time.time, on_progress=None):
    job = pipeline.load_job(job_dir)
    results = copy.deepcopy(SAMPLE)
    results['photos'][0]['category'] = job['photos'][0]['category']           # shows which settings the run got
    for ph in results['photos']: open(os.path.join(job_dir, ph['annotated']), 'wb').write(b'annotated')
    job.update(status='done', progress='', cost=results['cost'], finished=repo.now_iso())
    return job, results


pipeline.run = fake_run
store, c = web.store, web.app.test_client()


def check(name, ok, detail=''):
    print(('ok  ' if ok else 'FAIL'), name, detail if not ok else '')
    if not ok: check.failed = True


def wait(cid):
    for _ in range(200):
        runs = repo.list_runs(store, cid)
        if runs and all(r['status'] not in ('queued', 'running') for r in runs): return runs
        time.sleep(0.05)
    return repo.list_runs(store, cid)


def upload(data, name, category):
    r = c.post('/captures', data={'photos': [(__import__('io').BytesIO(data), name)], 'category': category}, content_type='multipart/form-data')
    return r.headers['Location'].split('/captures/')[1].split('?')[0]


check.failed = False
c.post('/login', data={'passcode': 'x'})
A, B = b'photo A' * 1000, b'photo B' * 1000

x = upload(A, 'shelf.jpg', 'rare'); wait(x)
x2 = upload(A, 'same shelf again.jpg', 'extreme'); runs = wait(x)
check('same photos, same capture', x2 == x, (x, x2))
check('two runs on it', len(runs) == 2, len(runs))
check('each run keeps its own settings', [r['photos'][0]['category'] for r in runs] == ['extreme', 'rare'], [r.get('photos') for r in runs])
check('the pipeline ran with the run\'s settings', repo.load_results(store, x, runs[0]['id'])['photos'][0]['category'] == 'extreme')
check('the capture keeps the latest settings', repo.load_capture(store, x)['photos'][0]['category'] == 'extreme')
check('capture photos are fingerprinted', repo.load_capture(store, x)['photos'][0]['sha256'] == repo.fingerprint([A])[0])
page = c.get(f'/captures/{x}').data.decode()
check('runs table shows each run\'s settings', 'Extremely rare' in page and 'Rare bookshop' in page and 'adds a run here' in page)

y = upload(B, 'other.jpg', 'cheap'); wait(y)
check('different photos, new capture', y != x)

# a duplicate made before fingerprints were kept: no sha256, its own run and a mark
z = repo.create_capture(store, [('old copy.jpg', A)], dict(category='signed', overrides={}, minimum=None, note='old', second_read=False))['id']
cap = repo.load_capture(store, z); [p.pop('sha256') for p in cap['photos']]; store.put_json(repo.capture_key(z), cap); index.update_entry(store, z)
zrun = web.queue_run(z); wait(z)
store.put_json(repo.run_key(z, zrun), {k: v for k, v in repo.load_run(store, z, zrun).items() if k != 'photos'})     # an old run
key = marks.book_key(1, SAMPLE['photos'][0]['pulls'][0]['book'])
r = c.post(f'/captures/{z}/marks', json={'run_id': zrun, 'key': key, 'verdict': 'worth'})
check('mark on the duplicate', r.status_code == 200, r.status_code)
check('index lists three captures before', len(index.read(store)['captures']) == 3)

log = []
found = consolidate.consolidate(store, apply=False, log=log.append)
check('dry run finds the pair, changes nothing', found == [[x, z]] and store.exists(repo.capture_key(z)), (found, log))
consolidate.consolidate(store, apply=True, log=log.append)
check('duplicate gone', not store.exists(repo.capture_key(z)))
runs = repo.list_runs(store, x)
check('its run moved in', len(runs) == 3 and any(r.get('merged_from') == z for r in runs), [r['id'] for r in runs])
moved = next(r for r in runs if r.get('merged_from') == z)
check('the moved run keeps the settings it ran with', moved['photos'][0]['category'] == 'signed', moved.get('photos'))
check('its results and files moved', repo.load_results(store, x, moved['id']) is not None
      and store.exists(repo.run_file_key(x, moved['id'], SAMPLE['photos'][0]['annotated'])))
ms, _ = marks.load(store, x)
check('its mark moved in', len(ms) == 1 and list(ms.values())[0]['verdict'] == 'worth', ms)
check('the mark shows on the newest run', any(m['verdict'] == 'worth' for m in marks.for_run(store, x, repo.load_results(store, x, runs[0]['id'])).values()))
r = c.get(f'/captures/{z}')
check('old link redirects', r.status_code == 301 and r.headers['Location'].endswith(f'/captures/{x}'), (r.status_code, r.headers.get('Location')))
check('index lists two captures after', sorted(e['id'] for e in index.read(store)['captures']) == sorted([x, y]), index.read(store))
check('second pass finds nothing', consolidate.consolidate(store, apply=True, log=log.append) == [])
sys.exit(1 if check.failed else 0)
