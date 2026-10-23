"""Copies the old jobs/<id> folders into the store as captures (see DESIGN.md): the same id, the photos and settings, and
one run holding the job's results and images (baseline null, since those runs predate git), with the job's marks.

Idempotent, so the app can call migrate() at every start: a migrated folder gets a .migrated marker, and a job whose
capture.json is already in the store is skipped. capture.json is written last, so a half-copied job is cleared and redone.

usage: python tools/migrate_jobs.py JOBS_DIR [--local STORE_DIR]   (else the store from open_store())"""
import argparse, datetime, json, os, secrets, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from shelfscan import index, repo
from shelfscan.store import LocalStore, open_store

SKIP = {'job.json', 'results.json', 'feedback.json', '.migrated'}
CAPTURE_FIELDS = ('file', 'name', 'category', 'overrides', 'minimum', 'note', 'second_read')
RUN_FIELDS = ('status', 'progress', 'error', 'cap', 'cost', 'started', 'finished')


def migrate(jobs_dir, store, log=print):
    """Migrates every job folder not yet in the store. Returns the ids migrated."""
    done = []
    for jid in sorted(os.listdir(jobs_dir)) if os.path.isdir(jobs_dir) else []:
        d = os.path.join(jobs_dir, jid)
        if not os.path.isfile(os.path.join(d, 'job.json')) or os.path.exists(os.path.join(d, '.migrated')): continue
        try:
            if not store.exists(repo.capture_key(jid)): migrate_one(d, jid, store, log)
            open(os.path.join(d, '.migrated'), 'w').write(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'))
            done.append(jid)
        except Exception as e:
            log(f'job {jid}: not migrated ({type(e).__name__}: {e})')
    return done


def _when(s):
    """An ISO time from job.json, in UTC (run ids are UTC); None if missing or malformed."""
    try: t = datetime.datetime.fromisoformat(s)
    except (TypeError, ValueError): return None
    return (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)).astimezone(datetime.timezone.utc)


def find_book(results, key):
    """The book an old feedback key ('photo:shelf:x0', photo 1-based) points to, among the photo's pulls, leads, their other
    copies and the books not pulled; None if it isn't there."""
    try:
        pi, shelf, x0 = key.split(':')
        ph = results['photos'][int(pi) - 1]
    except (ValueError, IndexError, KeyError, TypeError): return None
    for x in (ph.get('pulls') or []) + (ph.get('leads') or []) + (ph.get('not_pulled') or []):
        for c in [x] + (x.get('copies') or []):
            b = c.get('book') or {}
            if str(b.get('shelf')) == shelf and str(b.get('x0')) == x0: return b
    return None


def migrate_one(d, jid, store, log=print):
    job = json.load(open(os.path.join(d, 'job.json')))
    store.delete_prefix(f'captures/{jid}/')                 # anything left by a half-finished earlier attempt
    photos = job.get('photos') or []
    for p in photos:
        store.put_bytes(repo.photo_key(jid, p['file']), open(os.path.join(d, p['file']), 'rb').read())

    rp = os.path.join(d, 'results.json')
    results = json.load(open(rp)) if os.path.isfile(rp) else None
    rid = repo.new_run_id(_when(job.get('started')) or _when(job.get('created')) or _when(job.get('finished')))
    run = dict(id=rid, capture_id=jid, created=job.get('started') or job.get('created'), baseline=None,
               **{k: job.get(k) for k in RUN_FIELDS})
    if run['status'] in ('queued', 'running', None):         # cut off by the move; a new run can be started on the capture
        run.update(status='failed', error='stopped when scans moved to captures; start a new run')
    rph = (results or {}).get('photos') or []
    run.update(pulls=sum(len(p.get('pulls') or []) for p in rph) if results else None,
               leads=sum(len(p.get('leads') or []) for p in rph) if results else None,
               spines=sum(p.get('books') or 0 for p in rph) if results else None,
               unreadable=sum(p['unreadable'] for p in rph) if rph and all('unreadable' in p for p in rph) else None)
    if results is not None: store.put_json(repo.results_key(jid, rid), results)
    names = {p['file'] for p in photos}
    for name in sorted(os.listdir(d)):
        f = os.path.join(d, name)
        if name in SKIP or name in names or name.endswith('.tmp') or not os.path.isfile(f): continue
        store.put_bytes(repo.run_file_key(jid, rid, name), open(f, 'rb').read())
    store.put_json(repo.run_key(jid, rid), run)

    fp = os.path.join(d, 'feedback.json')
    old = json.load(open(fp)) if os.path.isfile(fp) else {}
    marks = {}
    for key, v in old.items():
        if not v.get('verdict'): continue
        b = find_book(results, key) if results else None
        mark = {k: v.get(k) for k in ('verdict', 'label', 'category', 'value', 'at')}
        mark['run_id'] = rid
        if b:
            mark['book'] = {'photo': int(key.split(':')[0]), **{k: b.get(k) for k in ('shelf', 'x0', 'x1', 'y0', 'y1', 'title', 'author')}}
        else:
            pi, shelf, x0 = (key.split(':') + ['', '', ''])[:3]
            mark['book'] = {'photo': int(pi) if pi.isdigit() else None, 'shelf': int(shelf) if shelf.isdigit() else None,
                            'x0': int(x0) if x0.lstrip('-').isdigit() else None, 'x1': None, 'y0': None, 'y1': None,
                            'title': v.get('label'), 'author': None}
            mark['note'] = 'book not found in the migrated run; only its label is known'
            log(f'job {jid}: mark {key} ({v.get("label")}) has no matching book; kept with its label only')
        marks[secrets.token_hex(6)] = mark
    if marks: store.put_json(repo.marks_key(jid), marks)

    store.put_json(repo.capture_key(jid), {'id': jid, 'created': job.get('created'),
                                           'photos': [{k: p.get(k) for k in CAPTURE_FIELDS} for p in photos]})
    index.update_entry(store, jid)
    log(f'job {jid}: migrated as capture {jid}, run {rid}, {len(marks)} mark{"s" if len(marks) != 1 else ""}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('jobs_dir')
    ap.add_argument('--local', help='a folder to use as the store, instead of open_store()')
    a = ap.parse_args()
    ids = migrate(a.jobs_dir, LocalStore(a.local) if a.local else open_store())
    print(f'{len(ids)} job{"s" if len(ids) != 1 else ""} migrated')
