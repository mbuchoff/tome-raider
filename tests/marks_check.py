"""Checks marks (set, change, clear, matching across runs), scoring and the marks routes, offline, on a local store.
Two runs of one capture are built from a real results file: the second shifts every box a little, rewords some titles,
swaps which copy of a title comes first, promotes a not-pulled book to a pull and loses one pulled book.
usage: python tests/marks_check.py [results.json]"""
import atexit, copy, json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
from flask import Flask, render_template_string
from shelfscan import evaluate, marks, marks_web, repo
from shelfscan.store import Conflict, LocalStore

SRC = sys.argv[1] if len(sys.argv) > 1 else '/tmp/claude-1000/-home-node-repos-norris/079c046b-3c36-4ac5-bc43-0762eabab3cb/scratchpad/fbcheck/r4-4ed2dbeb691f.json'
CID, RUN_A, RUN_B = 'cap0001', '20261022T230000Z-aaaa', '20261023T140000Z-bbbb'


def check(name, got, want):
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, got if ok else f'{got!r} (wanted {want!r})')
    if not ok: check.failed = True


check.failed = False


def item(b, reason=None):
    return {'book': b, 'assessment': {'value': None, 'reason': reason or '', 'signals': []}, 'category': 'cheap', 'label': b.get('title') or ''}


# run A: the real results, plus a not-pulled book and a second copy of the first pull, taken from books the run left out
run_a = json.load(open(SRC))
ph = run_a['photos'][0]
pulled = {(x['book']['shelf'], x['book']['x0']) for x in ph['pulls']}
spare = [b for b in ph['readings'] if (b['shelf'], b['x0']) not in pulled and len((b.get('title') or '').strip()) > 3 and 'unreadable' not in b['title']]
np_book, copy_book = spare[0], spare[1]
ph['not_pulled'] = [item(np_book, 'no signal')]
ph['pulls'][0]['copies'] = [item(copy_book)]
key_a = lambda b: marks.book_key(1, b)
p0, p1, p4 = (ph['pulls'][i]['book'] for i in (0, 1, 4))

# run B: every box shifted right by a tenth of its width and down 15 px, some titles reworded, the first pull's copies
# swapped, the not-pulled book promoted to a pull, and the fifth pull gone
run_b = copy.deepcopy(run_a)
qb = run_b['photos'][0]
reword = {p0['title']: p0['title'] + '?', np_book['title']: np_book['title'].upper()}
for b in qb['readings'] + [c['book'] for x in qb['pulls'] + qb['not_pulled'] for c in [x] + x.get('copies', [])]:
    w = b['x1'] - b['x0']
    b['title'] = reword.get(b.get('title'), b.get('title'))
    b['x0'], b['x1'], b['y0'], b['y1'] = b['x0'] + w // 10, b['x1'] + w // 10, b['y0'] + 15, b['y1'] + 15
lost = lambda b: b['shelf'] == p4['shelf'] and b.get('pos') == p4.get('pos')
qb['readings'] = [b for b in qb['readings'] if not lost(b)]
qb['pulls'] = [x for x in qb['pulls'] if not lost(x['book'])]
first = qb['pulls'][0]
first['book'], first['copies'][0]['book'] = first['copies'][0]['book'], first['book']
qb['pulls'].append(qb['not_pulled'].pop())
key_b = lambda b: marks.book_key(1, b)
q0_copy, q0_first, q_promoted = first['copies'][0]['book'], first['book'], qb['pulls'][-1]['book']

home = tempfile.mkdtemp()
atexit.register(shutil.rmtree, home, True)
store = LocalStore(home)
store.put_json(repo.capture_key(CID), {'id': CID, 'created': '2026-10-22T22:59:00+00:00', 'photos': [{'file': 'upload1.jpg', 'name': '19022.jpg'}]})
for rid, res, base in ((RUN_A, run_a, None), (RUN_B, run_b, {'sha': '0dec24c1b2df', 'committed_at': '2026-10-23T13:33:26-07:00', 'dirty': False})):
    store.put_json(repo.run_key(CID, rid), {'id': rid, 'capture_id': CID, 'status': 'done', 'baseline': base})
    store.put_json(repo.results_key(CID, rid), res)

# set, change, clear
r = marks.set_mark(store, CID, RUN_A, key_a(p0), 'worth', run_a)
check('set worth', (r['verdict'], r['marked']), ('worth', 1))
mid = r['id']
r = marks.set_mark(store, CID, RUN_A, key_a(p0), 'not', run_a)
check('change to not keeps the mark', (r['verdict'], r['id'], r['marked']), ('not', mid, 1))
check('should on a not-pulled book', marks.set_mark(store, CID, RUN_A, key_a(np_book), 'should', run_a)['marked'], 2)
marks.set_mark(store, CID, RUN_A, key_a(p1), 'worth', run_a)
check('clear (unanswer)', marks.set_mark(store, CID, RUN_A, key_a(p1), '', run_a), {'verdict': '', 'note': '', 'id': None, 'marked': 2})
# notes: a note alone keeps a mark that counts as unanswered; a verdict set later keeps the note; clearing the verdict keeps it
r = marks.set_mark(store, CID, RUN_A, key_a(p1), marks.KEEP, run_a, note=' first edition? check the jacket ')
check('note on an unanswered book', (r['verdict'], r['note'], r['marked']), ('', 'first edition? check the jacket', 2))
r = marks.set_mark(store, CID, RUN_A, key_a(p1), 'worth', run_a)
check('verdict keeps the note', (r['verdict'], r['note'], r['marked']), ('worth', 'first edition? check the jacket', 3))
r = marks.set_mark(store, CID, RUN_A, key_a(p1), '', run_a)
check('clearing the verdict keeps the note', (r['verdict'], r['note'], r['marked']), ('', 'first edition? check the jacket', 2))
check('a note-only mark is not scored', r['id'] in [o['id'] for o in evaluate.score(run_a, marks.load(store, CID)[0])['outcomes']], False)
r = marks.set_mark(store, CID, RUN_A, key_a(p1), marks.KEEP, run_a, note='')
check('removing the note removes the mark', (r['id'], r['marked']), (None, 2))
check('clearing an unanswered book changes nothing', marks.set_mark(store, CID, RUN_A, key_a(p1), None, run_a)['marked'], 2)
marks.set_mark(store, CID, RUN_A, key_a(p4), 'worth', run_a)
saved, _ = marks.load(store, CID)
check('stored book', {k: saved[mid]['book'][k] for k in ('photo', 'shelf', 'x0', 'title')}, {'photo': 1, 'shelf': p0['shelf'], 'x0': p0['x0'], 'title': p0['title']})
check('stored label, run and value', (saved[mid]['label'], saved[mid]['run_id'], saved[mid]['value']), (ph['pulls'][0]['label'], RUN_A, ph['pulls'][0]['assessment']['value']))
for bad, exc in (('maybe', ValueError), ('worth', KeyError)):
    try: marks.set_mark(store, CID, RUN_A, key_a(p0) if bad == 'maybe' else '1:9:99999', bad, run_a); check(f'{exc.__name__} raised', False, True)
    except exc: check(f'{exc.__name__} raised', True, True)

# matching into each run
fa = marks.for_run(store, CID, run_a)
check('run A: marks on their books', sorted((k, m['verdict']) for k, m in fa.items()),
      sorted([(key_a(p0), 'not'), (key_a(np_book), 'should'), (key_a(p4), 'worth')]))
fb = marks.for_run(store, CID, run_b)
check('run B: the mark follows its book, now a second copy', fb.get(key_b(q0_copy), {}).get('id'), mid)
check('run B: and shows on the entry carrying the buttons', fb.get(key_b(q0_first), {}).get('id'), mid)
check('run B: the reworded, promoted book keeps its mark', fb.get(key_b(q_promoted), {}).get('verdict'), 'should')
check('run B: the lost book has no match', any(m['verdict'] == 'worth' for m in fb.values()), False)

# scoring
sa, sb = evaluate.score(run_a, saved), evaluate.score(run_b, saved)
check('score run A', {k: sa[k] for k in ('agree', 'disagree', 'not found', 'unanswered')}, {'agree': 1, 'disagree': 2, 'not found': 0, 'unanswered': 3})
check('score run B', {k: sb[k] for k in ('agree', 'disagree', 'not found', 'unanswered')}, {'agree': 1, 'disagree': 1, 'not found': 1, 'unanswered': 3})
s = evaluate.summary(store)
c = s['captures'][0]
check('summary: latest and marked-on runs', [(r['run'], r['roles'], r['baseline_label']) for r in c['runs']],
      [(RUN_B, ['latest'], '2026-10-23 20:33 (0dec24c)'), (RUN_A, ['marked on'], 'before git')])
check('summary totals', (s['totals']['latest']['agree'], s['totals']['marked_on']['disagree']), (1, 2))


# a write racing another: the first save hits a changed etag, retries, and both marks survive
class Racing(LocalStore):
    raced = False

    def put_json(self, key, obj, if_match=None, if_none_match=False):
        if key.endswith('marks.json') and not self.raced:
            self.raced = True
            other, etag = self.get_json(key)
            other['zzz'] = dict(saved[mid], verdict='worth', book=dict(saved[mid]['book'], x0=1, x1=2))
            super().put_json(key, other, if_match=etag)
        return super().put_json(key, obj, if_match, if_none_match)


racing = Racing(home)
marks.set_mark(racing, CID, RUN_A, key_a(p1), 'worth', run_a)
after, _ = marks.load(store, CID)
check('etag conflict retried, both writes kept', ('zzz' in after, sum(1 for m in after.values() if m['book']['x0'] == p1['x0'])), (True, 1))

# the macros: unanswered until marked, then the pressed button and Clear
app = Flask(__name__, template_folder=os.path.join(HERE, '..', 'templates'))
with app.test_request_context():
    page = render_template_string("{% from '_marks.html' import mark_buttons %}{{ mark_buttons('1:2:3', item, None, 'pull') }}|"
                                  "{{ mark_buttons('1:2:3', item, {'verdict': 'should'}, 'not_pulled') }}", item=ph['pulls'][0])
    unanswered, marked = page.split('|')
check('unanswered: state shown, Clear hidden', ('class="fb-state">' in unanswered, 'class="fb-clear" hidden' in unanswered, ' on"' in unanswered), (True, True, False))
check('marked: button on, state hidden, Clear shown', ('fbb on' in marked, 'fb-state" hidden' in marked, 'class="fb-clear">' in marked), (True, True, True))

# the routes, with START_RUN stubbed
started = []
app.config.update(STORE=store, START_RUN=lambda cid: started.append(cid) or 'r')
app.add_url_rule('/', 'index', lambda: 'scan page')
app.register_blueprint(marks_web.bp)
web = app.test_client()
post = lambda body: web.post(f'/captures/{CID}/marks', json=body)
r = post({'run_id': RUN_B, 'key': key_b(qb['pulls'][1]['book']), 'verdict': 'worth'})
check('POST sets a mark', (r.status_code, r.get_json()['verdict']), (200, 'worth'))
r = post({'run_id': RUN_B, 'key': key_b(qb['pulls'][1]['book']), 'verdict': ''})
check('POST with no verdict clears it, and the mark set on run A with it', (r.status_code, r.get_json()['verdict'], r.get_json()['marked']), (200, '', len(after) - 1))
check('bad verdict', post({'run_id': RUN_B, 'key': key_b(q0_first), 'verdict': 'maybe'}).status_code, 400)
check('unknown book', post({'run_id': RUN_B, 'key': '1:9:1', 'verdict': 'worth'}).status_code, 404)
check('unknown run', post({'run_id': 'nope', 'key': key_b(q0_first), 'verdict': 'worth'}).status_code, 404)
check('/marks.json and /feedback.json', (len(web.get('/marks.json').get_json()['marks']), web.get('/feedback.json').status_code), (len(after) - 1, 200))
r = web.get('/eval')
check('/eval renders', (r.status_code, b'Runs against marks' in r.data, b'Re-run every marked capture' in r.data), (200, True, True))
r = web.post('/eval/rerun')
check('/eval/rerun starts a run per marked capture', (r.status_code, started), (302, [CID]))
sys.exit(1 if check.failed else 0)
