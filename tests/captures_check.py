"""Checks captures and runs end to end through the web app, with a folder store and a fake pipeline: no Claude calls.
usage: python tests/captures_check.py"""
import copy, glob, json, os, sys, tempfile, time
HOME = tempfile.mkdtemp()
os.environ.update(SHELFSCAN_HOME=HOME, SHELFSCAN_PASSCODE='x', SHELFSCAN_INSECURE_COOKIE='1', ANTHROPIC_API_KEY='unused')
for k in ('AZURE_STORAGE_CONNECTION_STRING', 'ANTHROPIC_ADMIN_KEY'): os.environ.pop(k, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as web
from shelfscan import pipeline, repo, version

OLD = sorted(glob.glob('/tmp/claude-1000/-home-node-repos-norris/079c046b-3c36-4ac5-bc43-0762eabab3cb/scratchpad/fbcheck/r4-*.json'))
SAMPLE = json.load(open(next(f for f in OLD if '61ef5c27550b' in f))) if OLD else None
fail_next = []


def fake_run(job_dir, claude, now=time.time, on_progress=None):
    """Stands in for the pipeline: an old run's results, with a file for every image they name."""
    job = pipeline.load_job(job_dir)
    job.update(status='running', started=repo.now_iso(), progress='reading')
    if on_progress: on_progress(job)
    if fail_next: raise RuntimeError(fail_next.pop())
    results = copy.deepcopy(SAMPLE)
    for ph in results['photos']:
        open(os.path.join(job_dir, ph['annotated']), 'wb').write(b'annotated')
        for x in ph['pulls'] + ph.get('leads', []):
            for c in [x] + x.get('copies', []):
                if c.get('thumb'): open(os.path.join(job_dir, c['thumb']), 'wb').write(b'thumb')
    job.update(status='done', progress='', cost=results['cost'], finished=repo.now_iso())
    if on_progress: on_progress(job)
    return job, results


pipeline.run = fake_run
store, c = web.store, web.app.test_client()


def check(name, ok, detail=''):
    print(('ok  ' if ok else 'FAIL'), name, detail if not ok else '')
    if not ok: check.failed = True


def wait(cid, rid, timeout=20):
    t = time.time()
    while time.time() - t < timeout:
        run = repo.load_run(store, cid, rid)
        if run['status'] not in ('queued', 'running'): return run
        time.sleep(0.1)
    return run


check.failed = False
check('needs sign-in', c.get('/').status_code == 302)
c.post('/login', data={'passcode': 'x'})
check('empty scan page', b'No captures yet' in c.get('/').data)

# upload -> capture -> first run
r = c.post('/captures', data={'photos': [(open(__file__, 'rb'), 'shelf one.jpg')], 'category': 'signed', 'overrides': '1=rare',
                              'note': 'test shelf', 'second_read': '1'}, content_type='multipart/form-data')
check('upload redirects to the capture', r.status_code == 302 and '/captures/' in r.headers['Location'], r.status_code)
cid = r.headers['Location'].rsplit('/', 1)[1]
capture = repo.load_capture(store, cid)
check('capture saved with its settings', capture['photos'][0]['category'] == 'signed' and capture['photos'][0]['overrides'] == {'1': 'rare'}
      and capture['photos'][0]['file'] == 'upload1.jpg' and capture['photos'][0]['name'] == 'shelf_one.jpg', capture)
run1 = wait(cid, repo.list_runs(store, cid)[0]['id'])
check('first run done', run1['status'] == 'done', run1)
check('run counts and baseline', run1['pulls'] == len(SAMPLE['photos'][0]['pulls']) and run1['spines'] == SAMPLE['photos'][0]['books']
      and (run1['baseline'] or {}).get('sha') == (version.baseline() or {}).get('sha'), run1)
check('images saved under the run', store.exists(repo.run_file_key(cid, run1['id'], SAMPLE['photos'][0]['annotated'])))
check('no working folder left', not os.listdir(web.WORK) if os.path.isdir(web.WORK) else True)
page = c.get(f'/captures/{cid}').data.decode()
check('capture page: runs table and results', 'Runs' in page and 'Run again with the current baseline' in page and 'Photo 1' in page
      and 'Worth it' in page and 'Should pull' in page and f'data-run="{run1["id"]}"' in page and 'test shelf' in page)
check('mark controls post to the capture', f'/captures/{cid}/marks' in page)

# a second run, on a different baseline
version.baseline = lambda: {'sha': 'f' * 40, 'committed_at': '2026-10-24T09:30:00+00:00', 'dirty': True}
r = c.post(f'/captures/{cid}/runs')
rid2 = r.headers['Location'].split('run=')[1]
run2 = wait(cid, rid2)
runs = repo.list_runs(store, cid)
check('second run with its own baseline', len(runs) == 2 and runs[0]['id'] == rid2 and run2['baseline']['sha'] == 'f' * 40
      and runs[1]['baseline']['sha'] != 'f' * 40, [r['baseline'] for r in runs])
page = c.get(f'/captures/{cid}').data.decode()
check('page shows both baselines, newest selected', 'fffffff' in page and 'uncommitted changes' in page and f'?run={run1["id"]}' in page)
check('older run selectable', c.get(f'/captures/{cid}?run={run1["id"]}').status_code == 200 and c.get(f'/captures/{cid}?run=nope').status_code == 404)

# files, photos and results stream from the store
f = c.get(f'/captures/{cid}/runs/{rid2}/files/{SAMPLE["photos"][0]["annotated"]}')
check('run file streams', f.status_code == 200 and f.data == b'annotated' and f.mimetype == 'image/jpeg', f.status_code)
check('photo streams', c.get(f'/captures/{cid}/photos/upload1.jpg').data == open(__file__, 'rb').read())
check('results.json streams', json.loads(c.get(f'/captures/{cid}/runs/{rid2}/results.json').data)['photos'][0]['name'] == SAMPLE['photos'][0]['name'])
check('missing file 404', c.get(f'/captures/{cid}/runs/{rid2}/files/none.jpg').status_code == 404)
check('missing capture 404', c.get('/captures/abcdef123456').status_code == 404 and c.get('/captures/..%2F..').status_code == 404)
r = c.get(f'/jobs/{cid}')
check('old job link redirects', r.status_code == 301 and r.headers['Location'].endswith(f'/captures/{cid}'))
check('old job file link redirects', c.get(f'/jobs/{cid}/files/x.jpg').status_code == 301)

# a failed run says why, and the capture keeps its earlier results
fail_next.append('boom')
rid3 = c.post(f'/captures/{cid}/runs').headers['Location'].split('run=')[1]
run3 = wait(cid, rid3)
check('failed run recorded', run3['status'] == 'failed' and 'boom' in run3['error'], run3)
check('failed run page shows the error', 'boom' in c.get(f'/captures/{cid}?run={rid3}').data.decode())

# the scan page lists captures (from the store while the index is a placeholder)
index_page = c.get('/').data.decode()
check('scan page lists the capture', f'/captures/{cid}' in index_page and 'Recent captures' in index_page)

# runs made before git, from every saved old results file, render
for path in OLD:
    old = json.load(open(path))
    oc = repo.create_capture(store, [('x.jpg', b'x')], dict(category='cheap', overrides={}, minimum=None, note='', second_read=False))
    orun = dict(id=repo.new_run_id(), capture_id=oc['id'], created='2026-10-22T12:00:00+00:00', started=None, finished=None, status='done',
                progress='', error=None, cap=2.0, cost=old.get('cost'), baseline=None, **repo.run_counts(old))
    repo.save_run(store, orun)
    store.put_json(repo.results_key(oc['id'], orun['id']), old)
    p = c.get(f'/captures/{oc["id"]}')
    check(f'old results render ({os.path.basename(path)})', p.status_code == 200 and 'before git' in p.data.decode(), p.status_code)

# restarting picks up a run left queued
queued = repo.start_run(store, cid, 2.0)
web.start()
check('queued run resumed after a restart', wait(cid, queued['id'])['status'] == 'done')
sys.exit(1 if check.failed else 0)
