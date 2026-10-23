"""captures/index.json: one summary per capture (see DESIGN.md), so the scan page reads one key, not one per capture.

Every change goes through an ETag retry: read the index with its etag, swap in the new summary, write with if_match."""
import random, time
from . import repo
from .store import Conflict, NotFound

RUN_FIELDS = ('id', 'status', 'created', 'pulls', 'leads', 'cost', 'baseline')


def summary(store, cid):
    """This capture's index entry from its capture.json, newest run.json and marks.json; None if there's no capture."""
    try: cap, _ = store.get_json(repo.capture_key(cid))
    except NotFound: return None
    runs = repo.list_runs(store, cid)                  # newest first by creation time; run ids only go to the second
    run = runs[0] if runs else None
    try: marks, _ = store.get_json(repo.marks_key(cid))
    except NotFound: marks = {}
    photos = cap.get('photos') or []
    used = repo.run_photos(cap, run)
    return {'id': cid, 'created': cap.get('created'), 'photos': len(photos), 'category': used[0].get('category') if used else None,
            'fingerprint': [p.get('sha256') for p in photos] if all(p.get('sha256') for p in photos) else None,
            'latest_run': {k: run.get(k) for k in RUN_FIELDS} if run else None,
            'marks': sum(1 for m in marks.values() if m.get('verdict'))}


def _write(store, change, tries=12):
    """Applies change(list of entries) -> list of entries to the index, retrying when another writer got there first."""
    for i in range(tries):
        try: idx, etag = store.get_json(repo.INDEX_KEY)
        except NotFound: idx, etag = {}, None
        caps = sorted(change(idx.get('captures') or []), key=lambda c: c.get('created') or '', reverse=True)
        try:
            store.put_json(repo.INDEX_KEY, {'captures': caps}, if_match=etag, if_none_match=etag is None)
            return caps
        except Conflict:
            time.sleep(random.uniform(0.05, 0.2) * (i + 1))
    raise Conflict(repo.INDEX_KEY)


def update_entry(store, cid):
    """Recomputes one capture's summary from its capture.json, latest run.json and marks.json, with ETag retries."""
    s = summary(store, cid)
    _write(store, lambda caps: [c for c in caps if c.get('id') != cid] + ([s] if s else []))


def read(store):
    """{'captures': [...]}, newest first."""
    try: return store.get_json(repo.INDEX_KEY)[0]
    except NotFound: return {'captures': []}


def rebuild(store):
    """Recomputes every summary from the captures in the store."""
    caps = [s for s in (summary(store, cid) for cid in repo.capture_ids(store)) if s]      # skips index.json and aliases.json
    _write(store, lambda _: caps)
