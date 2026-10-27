"""Captures and runs on top of the store (see DESIGN.md). Key helpers are fixed here so every module agrees on them."""
import datetime, hashlib, os, secrets
from . import version
from .store import NotFound


def capture_key(cid): return f'captures/{cid}/capture.json'
def photo_key(cid, name): return f'captures/{cid}/photos/{name}'
def marks_key(cid): return f'captures/{cid}/marks.json'
def run_key(cid, rid): return f'captures/{cid}/runs/{rid}/run.json'
def results_key(cid, rid): return f'captures/{cid}/runs/{rid}/results.json'
def run_file_key(cid, rid, name): return f'captures/{cid}/runs/{rid}/files/{name}'
INDEX_KEY = 'captures/index.json'
ALIASES_KEY = 'captures/aliases.json'           # {merged capture id -> the capture it was merged into}
SETTINGS = ('category', 'overrides', 'minimum', 'note', 'second_read')


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


def fingerprint(photos):
    """The photos' identity: the SHA-256 of each one's bytes, in order. The same photos make the same capture."""
    return [hashlib.sha256(data).hexdigest() for data in photos]


def find_capture(store, prints):
    """The id of the capture whose photos have these fingerprints, or None."""
    return next((c['id'] for c in _index().read(store)['captures'] if c.get('fingerprint') == prints), None)


def create_capture(store, photos, settings, cid=None, created=None):
    """photos: [(original file name, bytes)]; settings: category, overrides, minimum, note, second_read, shared by every
    photo. Writes the photos and capture.json, and returns the capture. The settings kept on the capture are the latest
    ones, used for the next run; each run keeps the settings it ran with."""
    cid = cid or new_capture_id()
    entries = []
    for i, (name, data) in enumerate(photos, 1):
        file = f'upload{i}{os.path.splitext(name or "")[1].lower() or ".jpg"}'
        store.put_bytes(photo_key(cid, file), data)
        entries.append(dict(file=file, name=name or file, sha256=hashlib.sha256(data).hexdigest(), **settings))
    capture = dict(id=cid, created=created or now_iso(), photos=entries)
    store.put_json(capture_key(cid), capture)
    _index().update_entry(store, cid)
    return capture


def load_capture(store, cid):
    return store.get_json(capture_key(cid))[0]


def update_settings(store, cid, settings):
    """Makes these the capture's settings for its next runs (a repeat upload of the same photos with new settings)."""
    capture = load_capture(store, cid)
    freeze_settings(store, capture)
    for p in capture['photos']: p.update({k: settings[k] for k in SETTINGS if k in settings})
    store.put_json(capture_key(cid), capture)
    return capture


def freeze_settings(store, capture):
    """Writes the capture's current settings onto any of its runs that don't record their own (runs from before runs kept
    them), so a later change of the capture's settings doesn't rewrite what those runs used."""
    for run in list_runs(store, capture['id']):
        if not run.get('photos'):
            run['photos'] = [{k: p.get(k) for k in ('file', 'name') + SETTINGS} for p in capture['photos']]
            save_run(store, run)


def matching_run(store, cid, baseline, capture=None):
    """The capture's newest run on this baseline (same commit, no uncommitted changes) with the capture's current settings,
    unless it failed or was stopped; None if there is none, so a new run would add something."""
    if not baseline or not baseline.get('sha') or baseline.get('dirty'): return None
    capture = capture or load_capture(store, cid)
    want = [{k: p.get(k) for k in SETTINGS} for p in capture['photos']]
    for run in list_runs(store, cid):
        b = run.get('baseline') or {}
        if (b.get('sha') == baseline['sha'] and not b.get('dirty') and run['status'] not in ('failed', 'stopped')
                and [{k: p.get(k) for k in SETTINGS} for p in run_photos(capture, run)] == want):
            return run
    return None


def run_photos(capture, run):
    """The photos with the settings a run used: its own (kept since captures were consolidated), else the capture's."""
    return (run or {}).get('photos') or capture['photos']


def resolve_alias(store, cid):
    """The capture a merged capture id now points to, or None."""
    try: return store.get_json(ALIASES_KEY)[0].get(cid)
    except NotFound: return None


def capture_ids(store):
    return [c for c in store.children('captures/') if valid_id(c)]


def get_photo(store, cid, name):
    return store.get_bytes(photo_key(cid, name))


def start_run(store, cid, photo_cap):
    """A queued run of the capture on the code as it stands now, with the capture's current settings, which the run keeps;
    the caller queues it for the worker. Its cap is photo_cap for every photo."""
    photos = [{k: p.get(k) for k in ('file', 'name') + SETTINGS} for p in load_capture(store, cid)['photos']]
    cap = round(photo_cap * len(photos), 2)
    run = dict(id=new_run_id(), capture_id=cid, created=now_iso('microseconds'), started=None, finished=None, status='queued',
               progress='waiting to start', error=None, cap=cap, cost=None, baseline=version.baseline(), photos=photos,
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
    return sorted(runs, key=_when, reverse=True)


def _when(run):
    """A run's creation time for sorting, from created (to the microsecond) or else from its id (to the second)."""
    try: t = datetime.datetime.fromisoformat(run['created'])
    except (KeyError, TypeError, ValueError):
        try: t = datetime.datetime.strptime(run['id'][:16], '%Y%m%dT%H%M%SZ').replace(tzinfo=datetime.timezone.utc)
        except (KeyError, ValueError): t = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    return t, run.get('id', '')


def latest_run(store, cid, done_only=False):
    return next((r for r in list_runs(store, cid) if not done_only or r['status'] not in ('queued', 'running')), None)


def load_results(store, cid, rid):
    try: return store.get_json(results_key(cid, rid))[0]
    except NotFound: return None


def photos_read(run, capture, results=None):
    """How many of the run's photos it read: on run.json since 27 October 2026; for older runs, from the results if given, or
    every photo of a run that finished; None while running or when it can't be told."""
    if run.get('photos_read') is not None: return run['photos_read']
    if results is not None: return len(results.get('photos') or [])
    return len(run_photos(capture, run)) if run.get('status') == 'done' else None


def put_run_file(store, cid, rid, name, data):
    store.put_bytes(run_file_key(cid, rid, name), data)


def get_run_file(store, cid, rid, name):
    return store.get_bytes(run_file_key(cid, rid, name))


def run_counts(results):
    """The counts kept on run.json, from a run's results."""
    ph = results.get('photos') or []
    return dict(photos_read=len(ph), pulls=sum(len(p.get('pulls') or []) for p in ph), leads=sum(len(p.get('leads') or []) for p in ph),
                spines=sum(p.get('books') or 0 for p in ph), unreadable=sum(p.get('unreadable') or 0 for p in ph))
