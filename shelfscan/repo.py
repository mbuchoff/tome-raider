"""Captures and runs on top of the store (see DESIGN.md). Key helpers are fixed here so every module agrees on them."""
import datetime, os, secrets
from . import version
from .store import NotFound


def capture_key(cid): return f'captures/{cid}/capture.json'
def photo_key(cid, name): return f'captures/{cid}/photos/{name}'
def marks_key(cid): return f'captures/{cid}/marks.json'
def run_key(cid, rid): return f'captures/{cid}/runs/{rid}/run.json'
def results_key(cid, rid): return f'captures/{cid}/runs/{rid}/results.json'
def run_file_key(cid, rid, name): return f'captures/{cid}/runs/{rid}/files/{name}'
INDEX_KEY = 'captures/index.json'


def new_capture_id():
    return secrets.token_hex(6)


def new_run_id(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(2)


def _index():
    from . import index            # imported late: the index module may use this module's key helpers
    return index


def now_iso(timespec='seconds'):
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec=timespec)


def valid_id(s):
    """Capture and run ids are hex, digits, 'T', 'Z' and '-': nothing that could climb out of a key."""
    return bool(s) and len(s) <= 40 and all(c.isalnum() or c == '-' for c in s)


def create_capture(store, photos, settings, cid=None, created=None):
    """photos: [(original file name, bytes)]; settings: category, overrides, minimum, note, second_read, shared by every
    photo. Writes the photos and capture.json, and returns the capture."""
    cid = cid or new_capture_id()
    entries = []
    for i, (name, data) in enumerate(photos, 1):
        file = f'upload{i}{os.path.splitext(name or "")[1].lower() or ".jpg"}'
        store.put_bytes(photo_key(cid, file), data)
        entries.append(dict(file=file, name=name or file, **settings))
    capture = dict(id=cid, created=created or now_iso(), photos=entries)
    store.put_json(capture_key(cid), capture)
    _index().update_entry(store, cid)
    return capture


def load_capture(store, cid):
    return store.get_json(capture_key(cid))[0]


def capture_ids(store):
    return [c for c in store.children('captures/') if valid_id(c)]


def get_photo(store, cid, name):
    return store.get_bytes(photo_key(cid, name))


def start_run(store, cid, cap):
    """A queued run of the capture on the code as it stands now; the caller queues it for the worker."""
    run = dict(id=new_run_id(), capture_id=cid, created=now_iso('microseconds'), started=None, finished=None, status='queued',
               progress='waiting to start', error=None, cap=cap, cost=None, baseline=version.baseline(),
               pulls=None, leads=None, spines=None, unreadable=None)
    save_run(store, run, update_index=True)
    return run


def load_run(store, cid, rid):
    return store.get_json(run_key(cid, rid))[0]


def save_run(store, run, update_index=False):
    """Writes run.json; update_index when the status changed, so the capture's summary follows."""
    store.put_json(run_key(run['capture_id'], run['id']), run)
    if update_index: _index().update_entry(store, run['capture_id'])


def list_runs(store, cid):
    """The capture's runs, newest first. Sorted by created, kept to the microsecond: run ids only go to the second."""
    runs = []
    for rid in store.children(f'captures/{cid}/runs/'):
        try: runs.append(load_run(store, cid, rid))
        except NotFound: pass                       # a run folder with no run.json yet
    return sorted(runs, key=lambda r: (datetime.datetime.fromisoformat(r['created']), r['id']), reverse=True)


def latest_run(store, cid, done_only=False):
    return next((r for r in list_runs(store, cid) if not done_only or r['status'] not in ('queued', 'running')), None)


def load_results(store, cid, rid):
    try: return store.get_json(results_key(cid, rid))[0]
    except NotFound: return None


def put_run_file(store, cid, rid, name, data):
    store.put_bytes(run_file_key(cid, rid, name), data)


def get_run_file(store, cid, rid, name):
    return store.get_bytes(run_file_key(cid, rid, name))


def summary(store, cid):
    """The capture's entry in the index, in DESIGN.md's shape. Used when the index has nothing yet."""
    capture = load_capture(store, cid)
    run = latest_run(store, cid)
    try: marks = len(store.get_json(marks_key(cid))[0])
    except NotFound: marks = 0
    p = capture['photos']
    return dict(id=cid, created=capture['created'], photos=len(p), category=p[0]['category'] if p else '', marks=marks,
                latest_run=run and {k: run.get(k) for k in ('id', 'status', 'created', 'pulls', 'leads', 'cost', 'baseline')})


def run_counts(results):
    """The counts kept on run.json, from a run's results."""
    ph = results.get('photos') or []
    return dict(pulls=sum(len(p.get('pulls') or []) for p in ph), leads=sum(len(p.get('leads') or []) for p in ph),
                spines=sum(p.get('books') or 0 for p in ph), unreadable=sum(p.get('unreadable') or 0 for p in ph))
