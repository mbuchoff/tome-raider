"""Merges captures whose photos are the same (byte for byte, in order) into the oldest of them: their runs and marks move
across, and the merged ids redirect to it. Also fills in the photo fingerprints of captures made before they were kept.

usage: python tools/consolidate.py [--apply] [--local DIR]     (without --apply it only reports what it would do)"""
import argparse, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from shelfscan import index, repo
from shelfscan.store import Conflict, NotFound, LocalStore, open_store


def backfill(store, write=True, log=print):
    """Fingerprints the photos of every capture that lacks them, saving them when write is set. Returns {capture id:
    fingerprint}."""
    out = {}
    for cid in repo.capture_ids(store):
        try: capture, etag = store.get_json(repo.capture_key(cid))
        except NotFound: continue
        if not all(p.get('sha256') for p in capture['photos']):
            prints = repo.fingerprint([repo.get_photo(store, cid, p['file']) for p in capture['photos']])
            for p, h in zip(capture['photos'], prints): p['sha256'] = h
            if write: store.put_json(repo.capture_key(cid), capture, if_match=etag)
            log(f'{cid}: fingerprinted {len(prints)} photo(s)' + ('' if write else ' (not saved: dry run)'))
        out[cid] = [p['sha256'] for p in capture['photos']]
    return out


def groups(store, write=True, log=print):
    """[[capture ids, oldest first]] for every set of captures with the same photos."""
    by_print = {}
    for cid, prints in backfill(store, write, log).items(): by_print.setdefault(tuple(prints), []).append(cid)
    def age(cid):        # capture times only go to the second; the first run's time breaks ties
        runs = repo.list_runs(store, cid)
        return repo.load_capture(store, cid).get('created') or '', repo._when(runs[-1]) if runs else repo._when({})
    return [sorted(ids, key=age) for ids in by_print.values() if len(ids) > 1]


def merge(store, keep, dup, log=print):
    """Moves dup's runs and marks into keep, points dup at keep, and deletes dup."""
    kept, gone = repo.load_capture(store, keep), repo.load_capture(store, dup)
    repo.freeze_settings(store, kept)
    repo.freeze_settings(store, gone)                   # each moved run keeps the settings it ran with
    for run in repo.list_runs(store, dup):
        rid = run['id'] if not store.exists(repo.run_key(keep, run['id'])) else repo.new_run_id()
        old_prefix, new_prefix = f'captures/{dup}/runs/{run["id"]}/', f'captures/{keep}/runs/{rid}/'
        for key in store.list(old_prefix):
            if not key.endswith('/run.json'): store.put_bytes(new_prefix + key[len(old_prefix):], store.get_bytes(key))
        repo.save_run(store, dict(run, id=rid, capture_id=keep, merged_from=dup))
        log(f'{dup}: run {run["id"]} moved to {keep}' + (f' as {rid}' if rid != run['id'] else ''))
    for _ in range(8):                                  # marks: the union; the photos are the same, so boxes still match
        try: theirs, _ = store.get_json(repo.marks_key(dup))
        except NotFound: theirs = {}
        try: ours, etag = store.get_json(repo.marks_key(keep))
        except NotFound: ours, etag = {}, None
        if not theirs: break
        merged = dict(theirs, **ours)
        try:
            if etag is None: store.put_json(repo.marks_key(keep), merged, if_none_match=True)
            else: store.put_json(repo.marks_key(keep), merged, if_match=etag)
            log(f'{dup}: {len(theirs)} mark(s) moved to {keep}')
            break
        except Conflict: continue
    for _ in range(8):
        try: aliases, etag = store.get_json(repo.ALIASES_KEY)
        except NotFound: aliases, etag = {}, None
        aliases = {k: (keep if v == dup else v) for k, v in aliases.items()}       # earlier merges into dup follow it
        aliases[dup] = keep
        try:
            if etag is None: store.put_json(repo.ALIASES_KEY, aliases, if_none_match=True)
            else: store.put_json(repo.ALIASES_KEY, aliases, if_match=etag)
            break
        except Conflict: continue
    store.delete_prefix(f'captures/{dup}/')
    index.rebuild(store)
    log(f'{dup}: merged into {keep} (created {gone.get("created")} -> {kept.get("created")})')


def consolidate(store, apply=False, log=print):
    """Merges every group of like captures into its oldest; returns the groups found."""
    found = groups(store, apply, log)
    for ids in found:
        log(('merging ' if apply else 'would merge ') + ', '.join(ids[1:]) + f' into {ids[0]}')
        if apply:
            for dup in ids[1:]: merge(store, ids[0], dup, log)
    if not found: log('no captures share their photos')
    if apply: index.rebuild(store)                    # picks up new fingerprints, so repeat uploads find their capture
    return found


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--local')
    a = ap.parse_args()
    consolidate(LocalStore(a.local) if a.local else open_store(), a.apply)
