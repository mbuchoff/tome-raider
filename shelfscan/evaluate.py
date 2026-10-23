"""Scoring runs against marks (see DESIGN.md), so a new baseline can be compared with an old one on the same marked captures.

A "Worth it" or "Should pull" mark agrees when the run pulls the book or lists it as a lead; a "Not worth it" mark agrees
when the run doesn't. Unanswered books don't count, but the pulls and leads nobody has marked are reported."""
from . import marks as marks_mod, version, repo
from .store import NotFound

OUTCOMES = ('agree', 'disagree', 'not found')


def score(results, marks):
    """dict(agree, disagree, not found, unanswered, outcomes): outcomes has one row per answered mark."""
    marks = {k: m for k, m in marks.items() if m.get('verdict')}            # a mark holding only a note is unanswered
    bs = marks_mod.books(results)
    by_key = {b['key']: b for b in bs}
    matched = marks_mod.match(marks, bs)
    rows = []
    for mid, m in marks.items():
        b = by_key.get(matched.get(mid))
        if b is None: outcome = 'not found'
        else: outcome = 'agree' if (b['status'] in ('pull', 'lead')) == (m['verdict'] in ('worth', 'should')) else 'disagree'
        rows.append(dict(id=mid, verdict=m['verdict'], note=m.get('note', ''), label=m.get('label', ''), outcome=outcome, status=b['status'] if b else None,
                         key=b['key'] if b else None))
    groups = {by_key[k]['group'] for k in matched.values()}
    unanswered = len({b['group'] for b in bs if b['status'] in ('pull', 'lead')} - groups)
    out = {o: sum(1 for r in rows if r['outcome'] == o) for o in OUTCOMES}
    return dict(out, unanswered=unanswered, outcomes=rows)


def _get(store, key):
    try: return store.get_json(key)[0]
    except NotFound: return None


def baseline_label(b):
    """'code of 2026-10-23 21:09 UTC (fc7706d)' or 'before git'."""
    if not b or not b.get('sha'): return 'before git'
    return f"code of {version.commit_time(b)} ({b['sha'][:7]})" + (' + changes' if b.get('dirty') else '')


def summary(store):
    """Every capture with marks: its latest finished run and the run(s) its marks were set on, each scored, plus totals for
    the latest runs and for the marked-on runs. dict(captures=[...], totals={'latest': {...}, 'marked_on': {...}})."""
    out, totals = [], {'latest': dict.fromkeys(OUTCOMES + ('unanswered',), 0), 'marked_on': dict.fromkeys(OUTCOMES + ('unanswered',), 0)}
    for cid in marks_mod.capture_ids(store):
        marks, _ = marks_mod.load(store, cid)
        marks = {k: m for k, m in marks.items() if m.get('verdict')}        # note-only marks aren't answers
        if not marks: continue
        capture = _get(store, repo.capture_key(cid)) or {}
        runs = []
        for rid in store.children(f'captures/{cid}/runs/'):
            r = _get(store, repo.run_key(cid, rid))
            if r: runs.append(dict(r, id=rid))
        done = [r for r in runs if r.get('status') == 'done']
        latest = max(done, key=lambda r: r['id'], default=None)
        marked_on = sorted({m.get('run_id') for m in marks.values() if m.get('run_id')} & {r['id'] for r in done})
        rows = []
        for r in ([latest] if latest else []) + [r for r in done if r['id'] in marked_on and r is not latest]:
            results = _get(store, repo.results_key(cid, r['id']))
            if results is None: continue
            s = score(results, marks)
            roles = (['latest'] if r is latest else []) + (['marked on'] if r['id'] in marked_on else [])
            rows.append(dict(run=r['id'], roles=roles, baseline=r.get('baseline'), baseline_label=baseline_label(r.get('baseline')),
                             **{k: s[k] for k in OUTCOMES + ('unanswered',)}, outcomes=s['outcomes']))
            for role, key in (('latest', 'latest'), ('marked on', 'marked_on')):
                if role in roles:
                    for k in OUTCOMES + ('unanswered',): totals[key][k] += s[k]
        out.append(dict(id=cid, created=capture.get('created', ''), photos=len(capture.get('photos') or []), marks=len(marks),
                        verdicts={v: sum(1 for m in marks.values() if m['verdict'] == v) for v in marks_mod.VERDICTS}, runs=rows))
    out.sort(key=lambda c: c['created'], reverse=True)
    return dict(captures=out, totals=totals)
