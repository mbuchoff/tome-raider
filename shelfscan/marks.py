"""Marks on the books of a capture (see DESIGN.md): set, clear, and match them to the books of any run.

A mark belongs to the capture, not to a run. It keeps the book as the run it was set on read it (photo, shelf, box, title,
author), and is matched to the same book in other runs by place and title. A book with no mark is unanswered; clearing a
mark makes it unanswered again."""
import datetime, difflib, re, secrets
from . import index, repo
from .store import Conflict, NotFound

VERDICTS = ('worth', 'not', 'should')
RETRIES = 8


def book_key(photo, b):
    return f"{photo}:{b['shelf']}:{b['x0']}"


def load(store, cid):
    """(marks, etag); ({}, None) when the capture has none yet."""
    try: return store.get_json(repo.marks_key(cid))
    except NotFound: return {}, None


def books(results):
    """Every book a run read, as dict(key, photo, book, item, status, group): status is 'pull' or 'lead' (also for their
    other copies), 'not_pulled', or 'skipped' for books left out silently; group is the key of the entry's first copy, which
    carries the mark buttons on the page. Old results without readings give only the listed books."""
    out = []
    for pi, ph in enumerate(results.get('photos') or [], 1):
        listed = {}
        for status in ('pull', 'lead'):
            for x in ph.get(status + 's') or []:
                for c in [x] + (x.get('copies') or []):
                    listed[book_key(pi, c['book'])] = dict(book=c['book'], item=c, status=status, group=book_key(pi, x['book']))
        for x in ph.get('not_pulled') or []:
            listed.setdefault(book_key(pi, x['book']), dict(book=x['book'], item=x, status='not_pulled', group=book_key(pi, x['book'])))
        for b in ph.get('readings') or []:
            k = book_key(pi, b)
            if k not in listed: listed[k] = dict(book=b, item=None, status='skipped', group=k)
        out += [dict(v, key=k, photo=pi) for k, v in listed.items()]
    return out


def _norm(s):
    s = (s or '').lower().replace('’', "'")
    if 'unreadable' in s: return ''
    s = re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', s)
    return re.sub(r'\s+', ' ', re.sub(r"[^a-z0-9' ]", ' ', s)).strip()


def _similar(a, b):
    """Titles that could be one book read twice; an unread title matches anything."""
    a, b = _norm(a), _norm(b)
    return not a or not b or a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def _overlap(a, b):
    """Shares of the narrower and of the shorter box the two have in common; a box without heights counts as overlapping."""
    ox = min(a['x1'], b['x1']) - max(a['x0'], b['x0'])
    fx = ox / max(1, min(a['x1'] - a['x0'], b['x1'] - b['x0'])) if ox > 0 else 0
    if None in (a.get('y0'), a.get('y1'), b.get('y0'), b.get('y1')): return fx, 1.0
    oy = min(a['y1'], b['y1']) - max(a['y0'], b['y0'])
    return fx, (oy / max(1, min(a['y1'] - a['y0'], b['y1'] - b['y0'])) if oy > 0 else 0)


def match(marks, candidates):
    """{mark id -> book key}: same photo, same or neighbouring shelf, boxes overlapping by half the narrower box across and a
    third of the shorter one down, similar titles or one unread. The best overlap wins; a book takes at most one mark."""
    pairs = []
    for mid, m in marks.items():
        mb = m.get('book') or {}
        if mb.get('x0') is None or mb.get('x1') is None: continue
        for c in candidates:
            b = c['book']
            if c['photo'] != mb.get('photo') or abs(b['shelf'] - mb.get('shelf', b['shelf'])) > 1: continue
            fx, fy = _overlap(mb, b)
            if fx >= 0.5 and fy >= 1 / 3 and _similar(mb.get('title'), b.get('title')):
                pairs.append((fx + fy + (0.5 if b['shelf'] == mb.get('shelf') else 0), mid, c['key']))
    out, taken = {}, set()
    for _, mid, k in sorted(pairs, reverse=True):
        if mid not in out and k not in taken: out[mid] = k; taken.add(k)
    return out


def for_run(store, cid, results, marks=None):
    """{book key 'photo:shelf:x0' -> mark (with its 'id')} for the books of this run that a mark matches. A mark on another
    copy of a title is also shown on the entry's first copy, which carries the buttons, unless that copy has its own mark."""
    if marks is None: marks, _ = load(store, cid)
    if not marks: return {}
    bs = books(results)
    by_key = {b['key']: b for b in bs}
    out = {k: dict(marks[mid], id=mid) for mid, k in match(marks, bs).items()}
    for k, m in list(out.items()):
        g = by_key[k]['group']
        if g != k and g not in out: out[g] = m
    return out


def set_mark(store, cid, run_id, key, verdict, results, now=None):
    """Sets the verdict on the book with this key in this run's results, or clears its mark when verdict is '' or None.
    Raises ValueError for an unknown verdict and KeyError for a key the run doesn't have. Returns dict(verdict, id, marked)."""
    verdict = verdict or ''
    if verdict and verdict not in VERDICTS: raise ValueError(verdict)
    target = next((b for b in books(results) if b['key'] == key), None)
    if target is None: raise KeyError(key)
    b, item = target['book'], target['item'] or {}
    for _ in range(RETRIES):
        marks, etag = load(store, cid)
        mid = next(iter(match(marks, [target])), None)          # the mark already on this book, if any
        if verdict:
            mid = mid or secrets.token_hex(6)
            marks[mid] = {'verdict': verdict, 'label': item.get('label') or (b.get('title') or ''), 'category': item.get('category', ''),
                          'value': (item.get('assessment') or {}).get('value'), 'run_id': run_id,
                          'at': (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec='seconds'),
                          'book': {'photo': target['photo'], 'shelf': b['shelf'], 'x0': b['x0'], 'x1': b['x1'], 'y0': b.get('y0'),
                                   'y1': b.get('y1'), 'title': b.get('title') or '', 'author': b.get('author') or ''}}
        elif mid: del marks[mid]
        else: return dict(verdict='', id=None, marked=len(marks))
        try:
            if etag is None: store.put_json(repo.marks_key(cid), marks, if_none_match=True)
            else: store.put_json(repo.marks_key(cid), marks, if_match=etag)
        except Conflict: continue
        index.update_entry(store, cid)
        return dict(verdict=verdict, id=mid if verdict else None, marked=len(marks))
    raise Conflict(repo.marks_key(cid))


def capture_ids(store):
    """The capture ids in the store (captures/index.json sits beside them and is skipped)."""
    return [c for c in store.children('captures/') if c.isalnum()]


def all_marks(store):
    """Every mark in the store, flat, with its capture and mark ids."""
    out = []
    for cid in capture_ids(store):
        marks, _ = load(store, cid)
        out += [dict(m, capture=cid, id=mid) for mid, m in marks.items()]
    return out
