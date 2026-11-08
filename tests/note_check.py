"""Checks the scan's note (scan_note): the books it's shown, what its reply marks, a bad reply, and the pipeline reading every
photo before applying the note once, then assessing with it; a scan with no note makes no call, and a failed note call
keeps the results. Made-up books; no Claude calls: fakes stand in.
usage: python tests/note_check.py"""
import json, os, sys, tempfile, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shelfscan import claude as cl, images, notes, pipeline, reader, rules, scan_note


def check(name, got, want=True):
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, '' if ok else f'got {got!r}, want {want!r}')
    if not ok: check.failed = True
check.failed = False


def bk(title, author, shelf=1, x0=0, y0=0): return dict(title=title, author=author, shelf=shelf, x0=x0, y0=y0)


class FakeClaude:
    def __init__(self, reply=None, fail=False): self.reply, self.fail, self.prompts = reply, fail, []
    def ask(self, model, image, prompt, max_tokens, worst_out, budget):
        self.prompts.append((model, image, prompt))
        if self.fail: raise RuntimeError('overloaded')
        return self.reply


# the books it's shown: photo by photo, shelf by shelf, left to right, with ids into each photo's readings
p1 = [bk('Foundation', 'Isaac Asimov', x0=900), bk('Harbor Nights', 'Hollis Prentiss', x0=100), bk('Low Tide', '', x0=300),
      bk('The Tin Lantern', 'Ada Wren', shelf=2, x0=50)]
p2 = [bk('(unreadable)', '(unreadable)', x0=10)]
text = scan_note.listing([(1, 'a.jpg', p1), (2, 'b.jpg', p2)])
check('listed shelf by shelf, left to right, with ids into the readings', [l.strip() for l in text.splitlines()],
      ['Photo 1 (a.jpg)', 'Shelf 1:', '[1.1] Harbor Nights, Hollis Prentiss', '[1.2] Low Tide, unread author', '[1.0] Foundation, Isaac Asimov',
       'Shelf 2:', '[1.3] The Tin Lantern, Ada Wren', 'Photo 2 (b.jpg)', 'Shelf 1:', '[2.0] unread title, unread author'])

# what the reply marks
note = 'Top shelf is signed up until the Asimov books, which are not'
reply = 'Here: ' + json.dumps({'books': [{'id': '1.1', 'is': 'signed'}, {'id': '1.2', 'is': 'Signed'}, {'id': '1.0', 'is': 'not signed'},
                                         {'id': '1.9', 'is': 'signed'}, {'id': '2.0', 'is': 'first edition'}, {'id': 'x', 'is': 'signed'}],
                              'summary': 'Photo 1, shelf 1: signed from Harbor Nights to Low Tide; Foundation is not.'})
c = FakeClaude(reply)
out = scan_note.apply(c, [(1, 'a.jpg', note, p1), (2, 'b.jpg', note, p2)], cl.Budget(1.0))
check('one Sonnet call, text only, for one note on two photos', [(m, i) for m, i, _ in c.prompts], [(cl.SONNET, None)])
check('the prompt holds the note and the books', note in c.prompts[0][2] and '[1.0] Foundation, Isaac Asimov' in c.prompts[0][2])
check('the books it names are marked; unknown ids and kinds are ignored', [b.get('from_note') for b in p1 + p2],
      ['not signed', 'signed', 'signed', None, None])
check('and each photo says what the note was taken to mean', out[1], {'summary': 'Photo 1, shelf 1: signed from Harbor Nights to Low Tide; Foundation is not.',
      'books': {'signed': ['Harbor Nights, Hollis Prentiss', 'Low Tide'], 'not signed': ['Foundation, Isaac Asimov']}})
check('a photo it names nothing on still gets the summary', out[2]['books'], {})
c = FakeClaude('not json')
check('a reply with no JSON marks nothing', scan_note.apply(c, [(1, 'a.jpg', 'shelf 2 is signed', [bk('Copper Bay', 'Ada Wren')])], cl.Budget(1.0)),
      {1: {'summary': '', 'books': {}}})
c = FakeClaude(json.dumps({'books': [], 'summary': ''}))
scan_note.apply(c, [(1, 'a.jpg', 'one', []), (2, 'b.jpg', 'two', [])], cl.Budget(1.0))
check('two different notes: one call each', len(c.prompts), 2)


# the pipeline: every photo read, then the note applied once, then each photo assessed with it
order = []
def fake_read(claude, im, budget, wants_second, progress=None, note=None):
    order.append(('read', im))
    return [{'shelf': 1}], [bk(f'Book {im}', 'Ada Wren')]
def fake_evaluate(books, shelf_category, ref, minimum_for, wants=None):
    order.append(('assess', books[0]['title'], books[0].get('from_note')))
    return [], [], [], {}
pipeline._ref = types.SimpleNamespace(version=lambda: 'test', rules={'minimum_value': {'cheap': 50}}, collections=None)
images.load, images.annotate = (lambda path: os.path.basename(path)), (lambda im, boxes: b'jpeg')
reader.read_photo, reader.check_feet, reader.date_copies, notes.add_notes = fake_read, (lambda *a: False), (lambda *a: 0), (lambda *a: 0)
rules.evaluate = fake_evaluate


def scan(note, claude):
    order.clear()
    d = tempfile.mkdtemp()
    pipeline.save_job(d, dict(id='x', created='2026-11-08T00:00:00+00:00', status='queued', cap=4.0,
                              photos=[dict(file=f, name=f, category='cheap', note=note) for f in ('a.jpg', 'b.jpg')]))
    return pipeline.run(d, claude)


c = FakeClaude(json.dumps({'books': [{'id': '2.0', 'is': 'signed'}], 'summary': 'Photo 2, shelf 1: all signed.'}))
job, results = scan('photo 2 is all signed', c)
check('both photos are read before the note, and the note before any assessing',
      [o[0] for o in order[:2]] + [len(c.prompts)] + [o[0] for o in order[2:]], ['read', 'read', 1, 'assess', 'assess'])
check('the assessing sees what the note says', sorted(o[1:] for o in order if o[0] == 'assess'), [('Book a.jpg', None), ('Book b.jpg', 'signed')])
check('each photo keeps what the note was taken to mean', [p.get('note_applied', {}).get('books') for p in results['photos']], [{}, {'signed': ['Book b.jpg, Ada Wren']}])
check('and the readings keep it, for free replays', results['photos'][1]['readings'][0]['from_note'], 'signed')
c = FakeClaude('{}')
job, results = scan('', c)
check('no note: no call, and nothing about a note in the results', (len(c.prompts), [('note_applied' in p) for p in results['photos']]), (0, [False, False]))
c = FakeClaude(fail=True)
job, results = scan('photo 2 is all signed', c)
check("a failed note call keeps the results and says the note wasn't applied", (job['status'], [p['note_applied'] for p in results['photos']]),
      ('done', [{'error': 'RuntimeError: overloaded'}] * 2))
sys.exit(1 if check.failed else 0)
