"""Checks captures and runs end to end through the web app, with a folder store and a fake pipeline: no Claude calls.
usage: python tests/captures_check.py"""
import copy, glob, io, json, os, re, sys, tempfile, time
HOME = tempfile.mkdtemp()
os.environ.update(SHELFSCAN_HOME=HOME, SHELFSCAN_PASSCODE='x', SHELFSCAN_INSECURE_COOKIE='1', ANTHROPIC_API_KEY='unused')
for k in ('AZURE_STORAGE_CONNECTION_STRING', 'ANTHROPIC_ADMIN_KEY'): os.environ.pop(k, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as web
from PIL import Image
from shelfscan import pipeline, repo, version

OLD = sorted(glob.glob('/tmp/claude-1000/-home-node-repos-norris/079c046b-3c36-4ac5-bc43-0762eabab3cb/scratchpad/fbcheck/r4-*.json'))
SAMPLE = json.load(open(next(f for f in OLD if '61ef5c27550b' in f))) if OLD else None
fail_next = []


def fake_run(job_dir, claude, now=time.time, on_progress=None):
    """Stands in for the pipeline: an old run's results for each photo, with a file for every image they name."""
    job = pipeline.load_job(job_dir)
    job.update(status='running', started=repo.now_iso(), progress='reading')
    if on_progress: on_progress(job)
    if fail_next: raise RuntimeError(fail_next.pop())
    results = copy.deepcopy(SAMPLE)
    results['photos'] = [copy.deepcopy(SAMPLE['photos'][0]) for _ in job['photos']]
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


def jpeg(w, h, orientation=None):
    """A real photo's bytes, w x h as stored; orientation is its EXIF orientation tag (6: shown turned a quarter clockwise)."""
    b, ex = io.BytesIO(), Image.Exif()
    if orientation: ex[0x0112] = orientation
    Image.new('RGB', (w, h), (120, 90, 60)).save(b, 'JPEG', exif=ex)
    return b.getvalue()


def links(page, cid):
    """The image pages a page links to, in order, each with the thumbnail inside its link (None when there's none)."""
    return [(int(m.group(1)), m.group(2), (re.search(r'<img [^>]*src="([^"]+)"', m.group(3)) or [None, None])[1])
            for m in re.finditer(r'<a [^>]*?href="/captures/%s/images/(\d+)(\?run=[^"]+)?"[^>]*>(.*?)</a>' % cid, page, re.S)]


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
r = c.get(f'/captures/{cid}')
check('a capture of one image opens at its image page', r.status_code == 302 and r.headers['Location'].endswith(f'/captures/{cid}/images/1'), r.status_code)
page = c.get(f'/captures/{cid}/images/1').data.decode()
check('which carries the runs table, the run button and the settings', 'Run against the current baseline' in page and 'test shelf' in page
      and '<table class="runs">' in page)
check('image page: the results, with mark controls for the run', f'data-run="{run1["id"]}"' in page and 'data-verdict="worth"' in page
      and 'data-verdict="should"' in page and f'/captures/{run1["capture_id"]}/runs/{run1["id"]}/files/{SAMPLE["photos"][0]["annotated"]}' in page)
check('mark controls post to the capture, keyed by image 1', f'/captures/{cid}/marks' in page and re.findall(r'data-key="(\d+):', page)
      and set(re.findall(r'data-key="(\d+):', page)) == {'1'})
check('no image 0, or past the last', c.get(f'/captures/{cid}/images/0').status_code == 404 and c.get(f'/captures/{cid}/images/2').status_code == 404)

# a second run, on a different baseline
version.baseline = lambda: {'sha': 'f' * 40, 'committed_at': '2026-10-24T09:30:00+00:00', 'dirty': True}
r = c.post(f'/captures/{cid}/runs')
rid2 = r.headers['Location'].split('run=')[1]
run2 = wait(cid, rid2)
runs = repo.list_runs(store, cid)
check('second run with its own baseline', len(runs) == 2 and runs[0]['id'] == rid2 and run2['baseline']['sha'] == 'f' * 40
      and runs[1]['baseline']['sha'] != 'f' * 40, [r['baseline'] for r in runs])
page = c.get(f'/captures/{cid}', follow_redirects=True).data.decode()
check('page shows both baselines, newest selected', 'fffffff' in page and 'uncommitted changes' in page and f'?run={run1["id"]}' in page)
r = c.get(f'/captures/{cid}?run={run1["id"]}')
check('a chosen run carries to the image page', r.headers['Location'].endswith(f'/images/1?run={run1["id"]}'), r.headers.get('Location'))
page = c.get(f'/captures/{cid}/images/1?run={run1["id"]}').data.decode()
check('the image page shows the chosen run, newest by default', f'data-run="{run1["id"]}"' in page
      and f'data-run="{rid2}"' in c.get(f'/captures/{cid}/images/1').data.decode())
check('and an unknown run is not found', c.get(f'/captures/{cid}/images/1?run=nope').status_code == 404)

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
check('failed run page shows the error', 'boom' in c.get(f'/captures/{cid}?run={rid3}', follow_redirects=True).data.decode())

# the scan page lists captures (from the store while the index is a placeholder)
index_page = c.get('/').data.decode()
check('scan page lists the capture', f'/captures/{cid}' in index_page and 'Recent captures' in index_page)
check('scan page: a thumbnail of the photo, linking to its image page', links(index_page, cid) == [(1, None, f'/captures/{cid}/thumbs/sm/upload1.jpg')],
      links(index_page, cid))
from shelfscan import index as capture_index
capture_index._write(store, lambda caps: [{k: v for k, v in e.items() if k != 'images'} for e in caps])
check('an index from before thumbnails is rebuilt', links(c.get('/').data.decode(), cid) == [(1, None, f'/captures/{cid}/thumbs/sm/upload1.jpg')])

# two photos: a gallery of two, the image pages lead from one to the other, and marks on image 2 are keyed by image 2
r = c.post('/captures', data={'photos': [(io.BytesIO(jpeg(1200, 800)), 'left.jpg'), (io.BytesIO(jpeg(1200, 800, orientation=6)), 'right.jpg')],
                              'category': 'rare'}, content_type='multipart/form-data')
cid2 = r.headers['Location'].rsplit('/', 1)[1]
two = wait(cid2, repo.list_runs(store, cid2)[0]['id'])
check('two-photo run done', two['status'] == 'done', two)
page = c.get(f'/captures/{cid2}').data.decode()
check('gallery: both images in order', [(n, t) for n, _, t in links(page, cid2)]
      == [(1, f'/captures/{cid2}/thumbs/md/upload1.jpg'), (2, f'/captures/{cid2}/thumbs/md/upload2.jpg')], links(page, cid2))
check('the capture page has the runs and leaves the results to the image pages', 'Run against the current baseline' in page
      and f'data-run="{two["id"]}"' not in page and 'data-verdict="' not in page)
page = c.get(f'/captures/{cid2}?run={two["id"]}').data.decode()
check('a chosen run carries to the gallery links', [q for _, q, _ in links(page, cid2)] == [f'?run={two["id"]}'] * 2, links(page, cid2))
p1, p2 = (c.get(f'/captures/{cid2}/images/{n}').data.decode() for n in (1, 2))
check('their image pages link back to the capture, and leave the runs table to it', f'href="/captures/{cid2}"' in p1 and '<table class="runs">' not in p1)
check('image 1 leads on to image 2, and image 2 back to 1', [n for n, _, _ in links(p1, cid2)] == [2] and [n for n, _, _ in links(p2, cid2)] == [1],
      ([n for n, _, _ in links(p1, cid2)], [n for n, _, _ in links(p2, cid2)]))
check('marks on image 2 are keyed by image 2', set(re.findall(r'data-key="(\d+):', p2)) == {'2'})
key2 = re.search(r'data-key="(2:[^"]+)"', p2).group(1)
r = c.post(f'/captures/{cid2}/marks', json={'run_id': two['id'], 'key': key2, 'verdict': 'worth'})
check('a mark set from image 2 shows on image 2 only', r.status_code == 200
      and f'data-key="{key2}"' in c.get(f'/captures/{cid2}/images/2').data.decode()
      and 'class="fbb on"' in c.get(f'/captures/{cid2}/images/2').data.decode() and 'class="fbb on"' not in c.get(f'/captures/{cid2}/images/1').data.decode())
index_page = c.get('/').data.decode()
check('scan page: both thumbnails, in order', [(n, t) for n, _, t in links(index_page, cid2)]
      == [(1, f'/captures/{cid2}/thumbs/sm/upload1.jpg'), (2, f'/captures/{cid2}/thumbs/sm/upload2.jpg')], links(index_page, cid2))

# thumbnails: made from the photo the right way up, small for the scan page and larger for the gallery, then kept
sm = c.get(f'/captures/{cid2}/thumbs/sm/upload1.jpg')
im = Image.open(io.BytesIO(sm.data)) if sm.status_code == 200 else None
check('small thumbnail: a JPEG, no more than 240 px, same shape', sm.status_code == 200 and sm.mimetype == 'image/jpeg' and im.format == 'JPEG'
      and max(im.size) <= 240 and abs(im.size[0] / im.size[1] - 1.5) < 0.02, (sm.status_code, im and im.size))
md = Image.open(io.BytesIO(c.get(f'/captures/{cid2}/thumbs/md/upload1.jpg').data))
check('gallery thumbnail is larger, but smaller than the photo', 240 < max(md.size) < 1200, md.size)
turned = Image.open(io.BytesIO(c.get(f'/captures/{cid2}/thumbs/sm/upload2.jpg').data))
check('a photo taken sideways is shown the right way up', turned.size[1] > turned.size[0], turned.size)
store.delete(repo.photo_key(cid2, 'upload1.jpg'))
check('a thumbnail is kept once made', c.get(f'/captures/{cid2}/thumbs/sm/upload1.jpg').data == sm.data)
check('a photo that is not an image, a missing photo or an unknown size: not found',
      [c.get(u).status_code for u in (f'/captures/{cid}/thumbs/sm/upload1.jpg', f'/captures/{cid2}/thumbs/sm/upload9.jpg',
                                      f'/captures/{cid2}/thumbs/huge/upload2.jpg', '/captures/abcdef123456/thumbs/sm/upload1.jpg')] == [404] * 4)

# a run still waiting: the image page shows the photo itself and refreshes, with no results yet
waiting = repo.start_run(store, cid2, 2.0)          # saved but never queued, so it stays waiting
page = c.get(f'/captures/{cid2}/images/2?run={waiting["id"]}').data.decode()
check('a waiting run: the photo, a refresh, no mark controls', 'http-equiv="refresh"' in page and f'/captures/{cid2}/photos/upload2.jpg' in page
      and 'data-verdict="' not in page, ('http-equiv="refresh"' in page, f'/captures/{cid2}/photos/upload2.jpg' in page, 'data-verdict="' in page))
run = repo.load_run(store, cid2, waiting['id']); run['status'] = 'stopped'; repo.save_run(store, run)

# runs made before git, from every saved old results file, render
for path in OLD:
    old = json.load(open(path))
    oc = repo.create_capture(store, [('x.jpg', b'x')], dict(category='cheap', overrides={}, minimum=None, note='', second_read=False))
    orun = dict(id=repo.new_run_id(), capture_id=oc['id'], created='2026-10-22T12:00:00+00:00', started=None, finished=None, status='done',
                progress='', error=None, cap=2.0, cost=old.get('cost'), baseline=None, **repo.run_counts(old))
    repo.save_run(store, orun)
    store.put_json(repo.results_key(oc['id'], orun['id']), old)
    p = c.get(f'/captures/{oc["id"]}', follow_redirects=True)
    imgs = [c.get(f'/captures/{oc["id"]}/images/1?run={orun["id"]}')]
    check(f'old results render ({os.path.basename(path)})', p.status_code == 200 and 'before git' in p.data.decode()
          and [i.status_code for i in imgs] == [200], (p.status_code, [i.status_code for i in imgs]))

# on shelves read in check order, a book's check lines show in the check-order table and not again on its card
listed = copy.deepcopy(SAMPLE)
for s in listed['photos'][0]['shelves']: s['check_order'] = True
listed['photos'][0]['pulls'][0]['assessment']['checks'] = ['Check for a signature: signed copies sold for a median of $987']
lc = repo.create_capture(store, [('x.jpg', b'x')], dict(category='signed', overrides={}, minimum=None, note='', second_read=False))
lrun = dict(id=repo.new_run_id(), capture_id=lc['id'], created='2026-10-29T12:00:00+00:00', started=None, finished=None, status='done',
            progress='', error=None, cap=2.0, cost=listed.get('cost'), baseline=None, **repo.run_counts(listed))
repo.save_run(store, lrun)
store.put_json(repo.results_key(lc['id'], lrun['id']), listed)
page = c.get(f'/captures/{lc["id"]}/images/1').data.decode()
check('check-order shelves: the signature line shows once', 'Check order:' in page and page.count('a median of $987') == 1, page.count('a median of $987'))

# marks: set on one run, shown on the page, carried to a newer run, cleared
pull = SAMPLE['photos'][0]['pulls'][0]['book']
key = f"1:{pull['shelf']}:{pull['x0']}"
r = c.post(f'/captures/{cid}/marks', json={'run_id': run1['id'], 'key': key, 'verdict': 'worth'})
check('mark saved', r.status_code == 200 and r.get_json()['verdict'] == 'worth', r.status_code)
page = c.get(f'/captures/{cid}/images/1?run={rid2}').data.decode()
check('mark shows on a newer run', f'data-key="{key}"' in page and 'class="fbb on" data-verdict="worth"' in page)
check('index counts the mark', next(e for e in web.recent_captures() if e['id'] == cid)['marks'] == 1)
r = c.post(f'/captures/{cid}/marks', json={'run_id': rid2, 'key': key, 'verdict': ''})
check('mark cleared', r.status_code == 200 and r.get_json()['verdict'] == '' and 'class="fbb on"' not in c.get(f'/captures/{cid}/images/1').data.decode())

# restarting migrates old job folders and picks up a run left queued
old_job = os.path.join(web.JOBS, 'a1b2c3d4e5f6')
os.makedirs(old_job)
open(os.path.join(old_job, 'upload1.jpg'), 'wb').write(b'old photo')
json.dump(dict(id='a1b2c3d4e5f6', created='2026-10-22T15:00:00+00:00', started='2026-10-22T15:00:01+00:00', status='done', cap=2.0,
               photos=[dict(file='upload1.jpg', name='old.jpg', category='rare', overrides={}, minimum=None, note='', second_read=True)]),
          open(os.path.join(old_job, 'job.json'), 'w'))
json.dump(SAMPLE, open(os.path.join(old_job, 'results.json'), 'w'))
json.dump({key: {'verdict': 'not', 'label': 'x', 'category': 'rare', 'value': None, 'at': '2026-10-22T16:00:00+00:00'}},
          open(os.path.join(old_job, 'feedback.json'), 'w'))
queued = repo.start_run(store, cid, 2.0)
web.start()
check('queued run resumed after a restart', wait(cid, queued['id'])['status'] == 'done')
mruns = repo.list_runs(store, 'a1b2c3d4e5f6')
check('old job migrated to a capture with a pre-git run', len(mruns) == 1 and mruns[0]['baseline'] is None and mruns[0]['status'] == 'done', mruns)
check('migrated mark shows', 'before git' in c.get('/captures/a1b2c3d4e5f6', follow_redirects=True).data.decode()
      and 'class="fbb on" data-verdict="not"' in c.get('/captures/a1b2c3d4e5f6/images/1').data.decode())
sys.exit(1 if check.failed else 0)
