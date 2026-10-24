"""Checks the store, the capture index and the migration of old jobs.

usage: python tests/store_check.py           offline: a LocalStore in a temporary folder
       python tests/store_check.py --live    also the real Blob container, under a throwaway test-<random>/ prefix that is
                                             deleted afterwards (needs AZURE_STORAGE_CONNECTION_STRING; never prints it)"""
import json, os, secrets, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from shelfscan import index, repo
from shelfscan.store import BlobStore, Conflict, LocalStore, NotFound
from tools import migrate_jobs

FIXTURES = '/tmp/claude-1000/-home-node-repos-norris/079c046b-3c36-4ac5-bc43-0762eabab3cb/scratchpad/fbcheck'


def check(name, got, want):
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, '' if ok else f'got {got!r}, wanted {want!r}')
    if not ok: check.failed = True


check.failed = False


def raises(name, exc, f, *a, **kw):
    try: f(*a, **kw)
    except exc: return check(name, True, True)
    except Exception as e: return check(name, type(e).__name__, exc.__name__)
    check(name, 'no error', exc.__name__)


def exercise(s, label):
    """Every store call, with the ETag rules the marks and the index rely on."""
    e1 = s.put_json('a/b.json', {'n': 1})
    obj, e2 = s.get_json('a/b.json')
    check(f'{label}: json round trip and etag', (obj, e1 == e2), ({'n': 1}, True))
    e3 = s.put_json('a/b.json', {'n': 2}, if_match=e2)
    check(f'{label}: write with the current etag', (s.get_json('a/b.json')[0], e3 != e2), ({'n': 2}, True))
    raises(f'{label}: stale etag is a conflict', Conflict, s.put_json, 'a/b.json', {'n': 3}, if_match=e2)
    raises(f'{label}: if_none_match on an existing key is a conflict', Conflict, s.put_json, 'a/b.json', {}, if_none_match=True)
    raises(f'{label}: if_match on a missing key is a conflict', Conflict, s.put_json, 'a/missing.json', {}, if_match=e3)
    s.put_json('a/new.json', {'k': 'v'}, if_none_match=True)
    check(f'{label}: if_none_match creates a new key', s.get_json('a/new.json')[0], {'k': 'v'})
    raises(f'{label}: missing key is NotFound (json)', NotFound, s.get_json, 'a/nope.json')
    raises(f'{label}: missing key is NotFound (bytes)', NotFound, s.get_bytes, 'a/nope.jpg')
    s.put_bytes('a/c/photo.jpg', b'\xff\xd8jpeg')
    check(f'{label}: bytes round trip', s.get_bytes('a/c/photo.jpg'), b'\xff\xd8jpeg')
    check(f'{label}: exists', (s.exists('a/c/photo.jpg'), s.exists('a/c/none.jpg')), (True, False))
    check(f'{label}: list is recursive and sorted', s.list('a/'), ['a/b.json', 'a/c/photo.jpg', 'a/new.json'])
    check(f'{label}: children are one level down', s.children('a/'), ['b.json', 'c', 'new.json'])
    s.delete('a/new.json'); s.delete('a/never-there.json')
    check(f'{label}: delete', s.exists('a/new.json'), False)
    s.delete_prefix('a/')
    check(f'{label}: delete_prefix', s.list('a/'), [])


def fake_job(root):
    """An old job folder in today's shapes: the real results and marks of one SME scan, with stand-in images."""
    results = json.load(open(os.path.join(FIXTURES, 'r4-4ed2dbeb691f.json')))
    feedback = {f['key']: {k: f[k] for k in ('verdict', 'label', 'category', 'value', 'at')}
                for f in json.load(open(os.path.join(FIXTURES, 'fb.json')))['feedback'] if f['job'] == '4ed2dbeb691f'}
    feedback['1:9:99999'] = {'verdict': 'should', 'label': 'A book no run saw', 'category': 'cheap', 'value': None, 'at': '2026-10-22T23:00:00+00:00'}
    d = os.path.join(root, '4ed2dbeb691f'); os.makedirs(d)
    job = {'id': '4ed2dbeb691f', 'created': '2026-10-22T22:28:02+00:00', 'status': 'done', 'cap': 2.0, 'progress': '',
           'started': '2026-10-22T22:28:03+00:00', 'finished': '2026-10-22T22:29:10+00:00', 'error': None,
           'cost': {'cap': 2.0, 'spent': 0.21, 'calls': 20, 'cached_calls': 0}, 'pulls': 5,
           'photos': [{'file': 'upload1.jpg', 'name': '19022.jpg', 'category': 'cheap', 'overrides': {}, 'minimum': None, 'note': '', 'second_read': True}]}
    json.dump(job, open(os.path.join(d, 'job.json'), 'w')); json.dump(results, open(os.path.join(d, 'results.json'), 'w'))
    json.dump(feedback, open(os.path.join(d, 'feedback.json'), 'w'))
    for name, data in (('upload1.jpg', b'photo'), ('photo1_annotated.jpg', b'annotated'), ('photo1_pull1.jpg', b'crop'), ('job.json.123.tmp', b'x')):
        open(os.path.join(d, name), 'wb').write(data)
    os.makedirs(os.path.join(root, 'not-a-job'))
    return results, feedback


def offline():
    exercise(LocalStore(tempfile.mkdtemp()), 'local')

    s = LocalStore(tempfile.mkdtemp())
    check('index: empty when missing', index.read(s), {'captures': []})
    for cid, created, cat in (('aaa', '2026-10-20T10:00:00+00:00', 'rare'), ('bbb', '2026-10-22T10:00:00+00:00', 'signed')):
        s.put_json(repo.capture_key(cid), {'id': cid, 'created': created, 'photos': [{'file': 'upload1.jpg', 'category': cat}]})
    s.put_json(repo.run_key('bbb', '20261022T100000Z-0001'), {'id': '20261022T100000Z-0001', 'status': 'done', 'pulls': 3,
                                                              'cost': {'spent': 0.21}})
    s.put_json(repo.run_key('bbb', '20261023T090000Z-0002'), {'id': '20261023T090000Z-0002', 'status': 'running', 'pulls': None,
                                                              'cost': {'spent': 0.05}, 'baseline': {'sha': 'abc', 'committed_at': None, 'dirty': False}})
    s.put_json(repo.marks_key('bbb'), {'m1': {'verdict': 'worth'}, 'm2': {'verdict': 'not'}, 'm3': {'verdict': ''}})
    index.update_entry(s, 'aaa'); index.update_entry(s, 'bbb')
    caps = index.read(s)['captures']
    check('index: newest capture first', [c['id'] for c in caps], ['bbb', 'aaa'])
    check('index: latest run is the newest run id', (caps[0]['latest_run']['id'], caps[0]['latest_run']['baseline']['sha']), ('20261023T090000Z-0002', 'abc'))
    check('index: marks count only answered ones', (caps[0]['marks'], caps[1]['marks']), (2, 0))
    check('index: photos and category', (caps[0]['photos'], caps[0]['category'], caps[1]['latest_run']), (1, 'signed', None))
    check('index: run count and total cost over every run', [(c['runs'], c['total_cost']) for c in caps], [(2, 0.26), (0, None)])
    index.update_entry(s, 'bbb')
    check('index: updating replaces, not duplicates', len(index.read(s)['captures']), 2)
    s.delete_prefix('captures/aaa/'); index.update_entry(s, 'aaa')
    check('index: a deleted capture drops out', [c['id'] for c in index.read(s)['captures']], ['bbb'])
    s.put_json(repo.INDEX_KEY, {'captures': []}); index.rebuild(s)
    check('index: rebuild from the captures', [c['id'] for c in index.read(s)['captures']], ['bbb'])
    real_put, calls = s.put_json, []

    def racing_put(key, obj, **kw):          # another writer slips in once between our read and our write
        if key == repo.INDEX_KEY and not calls:
            calls.append(1); real_put(key, {'captures': [{'id': 'zzz', 'created': '2026-10-01'}]})
        return real_put(key, obj, **kw)
    s.put_json = racing_put
    index.update_entry(s, 'bbb'); s.put_json = real_put
    check('index: a racing write is retried and kept', [c['id'] for c in index.read(s)['captures']], ['bbb', 'zzz'])

    jobs, s = tempfile.mkdtemp(), LocalStore(tempfile.mkdtemp())
    results, feedback = fake_job(jobs)
    logs = []
    check('migrate: migrates the job folder only', migrate_jobs.migrate(jobs, s, logs.append), ['4ed2dbeb691f'])
    cap = s.get_json(repo.capture_key('4ed2dbeb691f'))[0]
    check('migrate: capture keeps id, time and settings', (cap['id'], cap['created'], cap['photos'][0]['name'], cap['photos'][0]['category']),
          ('4ed2dbeb691f', '2026-10-22T22:28:02+00:00', '19022.jpg', 'cheap'))
    check('migrate: photo copied', s.get_bytes(repo.photo_key('4ed2dbeb691f', 'upload1.jpg')), b'photo')
    runs = s.children('captures/4ed2dbeb691f/runs/')
    check('migrate: one run, id from the start time', (len(runs), runs[0][:16]), (1, '20261022T222803Z'))
    rid = runs[0]
    run = s.get_json(repo.run_key('4ed2dbeb691f', rid))[0]
    ph = results['photos'][0]
    check('migrate: run counts and baseline', (run['status'], run['baseline'], run['pulls'], run['leads'], run['spines'], run['unreadable'], run['cost']['spent']),
          ('done', None, len(ph['pulls']), len(ph['leads']), ph['books'], ph['unreadable'], 0.21))
    check('migrate: results copied', s.get_json(repo.results_key('4ed2dbeb691f', rid))[0], results)
    check('migrate: run files are the images only', s.list(f'captures/4ed2dbeb691f/runs/{rid}/files/'),
          [repo.run_file_key('4ed2dbeb691f', rid, n) for n in ('photo1_annotated.jpg', 'photo1_pull1.jpg')])
    marks = s.get_json(repo.marks_key('4ed2dbeb691f'))[0]
    check('migrate: every answered mark kept', len(marks), len(feedback))
    by_label = {m['label']: m for m in marks.values()}
    flowers = by_label['Flowers, Irving Penn']
    pulled = next(x['book'] for x in ph['pulls'] if x['book']['title'] == 'Flowers')
    check('migrate: mark matched to its book with box and title', (flowers['verdict'], flowers['run_id'], flowers['book']),
          ('worth', rid, {'photo': 1, 'shelf': pulled['shelf'], 'x0': pulled['x0'], 'x1': pulled['x1'], 'y0': pulled['y0'], 'y1': pulled['y1'],
                          'title': 'Flowers', 'author': 'Irving Penn'}))
    lost = by_label['A book no run saw']
    check('migrate: unmatched mark kept with its label and a note', (lost['book']['title'], lost['book']['shelf'], 'note' in lost, any('no matching book' in l for l in logs)),
          ('A book no run saw', 9, True, True))
    entry = index.read(s)['captures'][0]
    check('migrate: index entry', (entry['id'], entry['marks'], entry['latest_run']['id']), ('4ed2dbeb691f', len(feedback), rid))
    check('migrate: second pass does nothing', (migrate_jobs.migrate(jobs, s, logs.append), len(s.children('captures/4ed2dbeb691f/runs/'))), ([], 1))
    os.remove(os.path.join(jobs, '4ed2dbeb691f', '.migrated'))
    check('migrate: an already-stored capture is skipped, not copied again', (migrate_jobs.migrate(jobs, s, logs.append), len(s.children('captures/4ed2dbeb691f/runs/'))), (['4ed2dbeb691f'], 1))

    # a half-finished earlier attempt (no capture.json yet) is cleared and redone
    jobs2, s2 = tempfile.mkdtemp(), LocalStore(tempfile.mkdtemp())
    fake_job(jobs2)
    s2.put_bytes('captures/4ed2dbeb691f/runs/20260101T000000Z-dead/files/old.jpg', b'left over')
    migrate_jobs.migrate(jobs2, s2, logs.append)
    check('migrate: leftovers from a failed attempt are cleared', len(s2.children('captures/4ed2dbeb691f/runs/')), 1)


def live():
    cs = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
    if not cs: return check('live: AZURE_STORAGE_CONNECTION_STRING is set', False, True)
    prefix = f'test-{secrets.token_hex(4)}/'
    s = BlobStore(cs, os.environ.get('SHELFSCAN_CONTAINER') or 'shelfscan', prefix=prefix)
    try:
        exercise(s, 'blob')
        s.put_bytes('t/photo.jpg', b'x'); s.put_json('t/r.json', {})
        props = {k: s.container.get_blob_client(prefix + k).get_blob_properties().content_settings.content_type for k in ('t/photo.jpg', 't/r.json')}
        check('blob: content types', props, {'t/photo.jpg': 'image/jpeg', 't/r.json': 'application/json'})
        jobs = tempfile.mkdtemp(); fake_job(jobs)
        migrate_jobs.migrate(jobs, s, lambda m: None)
        check('blob: migration and index', [c['id'] for c in index.read(s)['captures']], ['4ed2dbeb691f'])
    finally:
        s.delete_prefix('')
        check('blob: test prefix removed', s.list(''), [])


if __name__ == '__main__':
    offline()
    if '--live' in sys.argv: live()
    sys.exit(1 if check.failed else 0)
