"""Checks the runs table's baselines and the "Run against the current baseline" button: it names the current commit and
is disabled while the capture already has a run on it with the same settings. Folder store, fake pipeline, no Claude calls.
usage: python tests/baseline_check.py"""
import copy, glob, io, json, os, sys, tempfile, time
HOME = tempfile.mkdtemp()
os.environ.update(SHELFSCAN_HOME=HOME, SHELFSCAN_PASSCODE='x', SHELFSCAN_INSECURE_COOKIE='1', ANTHROPIC_API_KEY='unused')
for k in ('AZURE_STORAGE_CONNECTION_STRING', 'ANTHROPIC_ADMIN_KEY'): os.environ.pop(k, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as web
from shelfscan import pipeline, repo, version

SAMPLE = json.load(open(glob.glob('/tmp/claude-1000/-home-node-repos-norris/079c046b-3c36-4ac5-bc43-0762eabab3cb/scratchpad/fbcheck/r4-4ed2dbeb691f.json')[0]))
fail = []


def fake_run(job_dir, claude, now=time.time, on_progress=None):
    job = pipeline.load_job(job_dir)
    if fail: raise RuntimeError(fail.pop())
    results = copy.deepcopy(SAMPLE)
    for ph in results['photos']: open(os.path.join(job_dir, ph['annotated']), 'wb').write(b'annotated')
    job.update(status='done', progress='', cost=results['cost'], finished=repo.now_iso())
    return job, results


pipeline.run = fake_run
current = {}
version.baseline = lambda: current.get('b')
store, c = web.store, web.app.test_client()
A = {'sha': 'a' * 40, 'committed_at': '2026-10-23T14:29:00-07:00', 'dirty': False}
B = {'sha': 'b' * 40, 'committed_at': '2026-10-24T09:00:00+00:00', 'dirty': False}


def check(name, ok, detail=''):
    print(('ok  ' if ok else 'FAIL'), name, '' if ok else detail)
    if not ok: check.failed = True


def wait(cid):
    for _ in range(200):
        runs = repo.list_runs(store, cid)
        if runs and all(r['status'] not in ('queued', 'running') for r in runs): return runs
        time.sleep(0.05)
    return repo.list_runs(store, cid)


def upload(category):
    r = c.post('/captures', data={'photos': [(io.BytesIO(b'one photo' * 500), 'shelf.jpg')], 'category': category}, content_type='multipart/form-data')
    return r.headers['Location'].split('/captures/')[1].split('?')[0]


check.failed = False
c.post('/login', data={'passcode': 'x'})
current['b'] = A
cid = upload('rare'); runs = wait(cid)
page = c.get(f'/captures/{cid}').data.decode()
check('runs table: the current baseline reads "latest"', '<th>When (UTC)</th><th>Settings</th><th>Baseline (UTC)</th>' in page
      and '<td><span title="2026-10-23 21:29 UTC, commit ' + A['sha'] + '">latest</span></td>' in page, page[:0])
check('the run header gives its time and SHA, without UTC', '· baseline <span title="commit ' + A['sha'] + '">2026-10-23 21:29</span> (<code class="small" title="'
      + A['sha'] + '">aaaaaaa</code>)' in page)
check('the scan page says "latest" too', '">latest</span></td>' in c.get('/').data.decode())
check('the button names the current baseline', 'Run against the current baseline (aaaaaaa)</button>' in page)
check('and is disabled: this capture already ran on it', '<button class="secondary" type="submit" disabled title="This capture already has a run on aaaaaaa' in page)
r = c.post(f'/captures/{cid}/runs')
check('posting anyway starts nothing', len(wait(cid)) == 1 and runs[0]['id'] in r.headers['Location'], r.headers.get('Location'))

current['b'] = B
page = c.get(f'/captures/{cid}').data.decode()
check('a new commit enables it', 'type="submit">Run against the current baseline (bbbbbbb)</button>' in page)
check('and the older run shows its time and SHA', '>latest<' not in page and '<td><span title="commit ' + A['sha'] + '">2026-10-23 21:29</span> (<code' in page
      and '>latest<' not in c.get('/').data.decode())
c.post(f'/captures/{cid}/runs'); runs = wait(cid)
check('and runs it', len(runs) == 2 and runs[0]['baseline']['sha'] == B['sha'])
check('disabled again after', 'type="submit" disabled title=' in c.get(f'/captures/{cid}').data.decode())
index = c.get('/').data.decode()
check('scan page: renamed columns', '<th>Runs</th><th>Pulls</th><th>Marked</th><th>Total Cost</th><th>Latest Baseline (UTC)</th>' in index)
check('scan page: two runs, and the cost of both', '<td>2</td>' in index and f"<td>${2 * SAMPLE['cost']['spent']:.2f}</td>" in index, SAMPLE['cost'])
from shelfscan import index as capture_index
capture_index._write(store, lambda caps: [{k: v for k, v in e.items() if k not in ('runs', 'total_cost')} for e in caps])
check('an index from before run counts is rebuilt', [e.get('runs') for e in web.recent_captures()] == [2])

upload('extreme'); runs = wait(cid)
check('a repeat upload with new settings still runs', len(runs) == 3 and runs[0]['photos'][0]['category'] == 'extreme')
upload('extreme'); runs = wait(cid)
check('the same upload again runs nothing new', len(runs) == 3)

current['b'] = dict(B, sha='c' * 40); fail.append('boom')
c.post(f'/captures/{cid}/runs'); runs = wait(cid)
check('a failed run on the baseline', runs[0]['status'] == 'failed')
check('the scan page says the latest failed', '<td>4 <span class="status failed">(latest failed)</span></td>' in c.get('/').data.decode())
check('leaves the button enabled to retry', 'type="submit">Run against' in c.get(f'/captures/{cid}').data.decode())
current['b'] = dict(B, dirty=True)
page = c.get(f'/captures/{cid}').data.decode()
check('uncommitted changes never count as run', 'type="submit">Run against' in page)
check('nor as "latest"', '>latest<' not in page)
current['b'] = None
page = c.get(f'/captures/{cid}').data.decode()
check('no git: enabled, without a SHA', 'type="submit">Run against the current baseline</button>' in page)
sys.exit(1 if check.failed else 0)
