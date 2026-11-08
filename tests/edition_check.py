"""Checks the publisher hints and the leather edition check of 8 November 2026: the shelf setting's publishers, a shelf's sign,
the scan's note naming a publisher, and comparing a spine with sale photos (fake Claude and photos; no shop data, no calls).
usage: python tests/edition_check.py"""
import json, os, sys, tempfile
os.environ['SHELFSCAN_HOME'] = tempfile.mkdtemp()
for k in ('AZURE_STORAGE_CONNECTION_STRING',): os.environ.pop(k, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from PIL import Image
from shelfscan import edition_check, pipeline, scan_note


def check(name, got, want=True):
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, '' if ok else f'got {got!r}, want {want!r}')
    if not ok: check.failed = True
check.failed = False

# the shelf field: categories as before, and publishers whose sales value only their own copies
text = '3=signed, 4=Easton Press, 5-6 franklin, 2=rare, 7=Folio Society, 8=Penguin'
check('categories are read as before', pipeline.parse_overrides(text), {3: 'signed', 2: 'rare'})
check('publishers: Easton, Franklin (the word alone), a specialty publisher; not an ordinary one',
      pipeline.parse_publishers(text), {4: 'Easton Press', 5: 'Franklin Library', 6: 'Franklin Library', 7: 'Folio Society'})


def book(shelf, title):
    return dict(shelf=shelf, x0=10, x1=40, y0=10, y1=190, title=title, author='Ada Wren')

books = [book(1, 'The Salt Road'), book(2, 'Copper Bay'), book(3, 'Glass Orchard')]
bands = [dict(shelf=1, sign='EASTON PRESS'), dict(shelf=2, sign='Easton Press'), dict(shelf=3, sign='Mysteries')]
pipeline.publisher_hints({'publishers': {'2': 'Franklin Library'}}, bands, books)
check("a shelf's sign names the publisher", books[0].get('publisher_hint'), {'publisher': 'Easton Press', 'from': 'the shelf\'s sign ("EASTON PRESS")'})
check("the user's setting outranks a sign", books[1]['publisher_hint']['publisher'], 'Franklin Library')
check('a sign naming no such publisher gives none', 'publisher_hint' in books[2], False)


class Fake:
    def __init__(self, reply): self.reply, self.calls = reply, []
    def ask(self, model, image, prompt, max_tokens, worst_out, budget, parts=(), effort=None):
        self.calls.append(dict(model=model, parts=parts, effort=effort, prompt=prompt))
        return json.dumps(self.reply)

# the note names a publisher's section; it doesn't overrule the shelf setting
fake = Fake({'books': [{'id': '1.0', 'is': 'publisher', 'publisher': 'Easton Press'}, {'id': '1.1', 'is': 'publisher', 'publisher': 'Easton Press'},
                       {'id': '1.2', 'is': 'publisher', 'publisher': 'Penguin'}], 'summary': 'shelves 1 and 2 are Easton Press'})
out = scan_note.apply(fake, [(1, 'p.jpg', 'the top two shelves are Easton Press', books)], None)
check("the note's publisher goes on the book, over a sign's", books[0]['publisher_hint'], {'publisher': 'Easton Press', 'from': "the scan's note"})
check('but not over the shelf setting', books[1]['publisher_hint']['from'], 'the shelf setting')
check('a publisher whose sales value any copy is ignored', 'publisher_hint' in books[2], False)
check('the results say what the note was taken to mean', list(out[1]['books']), ['Easton Press'])

# comparing a spine with its title's sale photos
sales = [dict(title='The Salt Road Easton Press', price=150.0, image='https://i.ebayimg.com/images/g/AAAA1111/s-l1600.jpg', publisher='Easton Press'),
         dict(title='The Salt Road Franklin Library', price=90.0, image='https://i.ebayimg.com/images/g/BBBB2222/s-l1600.jpg', publisher='Franklin Library'),
         dict(title='The Salt Road first edition', price=70.0, image='https://i.ebayimg.com/images/g/CCCC3333/s-l1600.jpg')]
im = Image.new('RGB', (200, 200), 'green')
gone = 'BBBB2222'
def fetch(sale): return None if gone in sale['image'] else b'\xff\xd8 photo'
fake = Fake({'A': {'same': 'yes', 'why': 'same green leather'}, 'B': {'same': 'no', 'why': 'cloth'}})
b = book(1, 'The Salt Road')
edition_check.compare(fake, im, b, sales, None, fetch=fetch)
c = fake.calls[0]
check('one Haiku call at high effort', (c['model'], c['effort']), ('claude-haiku-5-5', 'high'))
check('the spine, then each photo that could be had, lettered in order', [p if isinstance(p, str) else 'JPEG' for p in c['parts']],
      ['Shelf spine:', 'JPEG', 'Sold listing A: "The Salt Road Easton Press"', 'JPEG', 'Sold listing B: "The Salt Road first edition"', 'JPEG'])
check('the answers go with the right sales; a photo that could not be had is not compared',
      [(x['id'], x['same']) for x in b['edition_check']['compared']], [('AAAA1111', 'yes'), ('CCCC3333', 'no')])
check('and the matches are kept for the rules', b['edition_check']['matched'], ['AAAA1111'])
b = book(1, 'The Salt Road')
edition_check.compare(Fake({}), im, b, sales, None, fetch=lambda s: None)
check('no photos to be had: no call, and nothing compared', b['edition_check'], dict(model='claude-haiku-5-5', compared=[], matched=[]))
class Broken(Fake):
    def ask(self, *a, **kw): raise RuntimeError('API down')
b = book(1, 'The Salt Road')
check('a failed call leaves the copy uncompared, to be listed to check', (edition_check.run(Broken({}), im, [(b, sales)], None, fetch=fetch), 'edition_check' in b), (0, False))

# a copy valued from first editions: the jacket prompt, and each answer keeps its sale's kind
fake = Fake({'A': {'same': 'no', 'why': 'UK jacket'}, 'B': {'same': 'yes', 'why': 'same jacket'}})
b = book(1, 'The Salt Road')
edition_check.compare_first(fake, im, b, [('first', sales[0]), ('first', sales[1]), ('book club', sales[2])], None, fetch=fetch)
check('the jacket prompt is used', 'dust-jacket design' in fake.calls[0].get('prompt', ''), True)
check("each answer keeps its sale's kind; a photo that couldn't be had is left out",
      [(c['id'], c['kind'], c['same']) for c in b['first_photos']['compared']], [('AAAA1111', 'first', 'no'), ('CCCC3333', 'book club', 'yes')])
b = book(1, 'The Salt Road')
check('run compares with the function it is given', (edition_check.run(fake, im, [(b, [('first', sales[0])])], None, fetch=fetch, how=edition_check.compare_first),
                                                     'first_photos' in b, 'edition_check' in b), (1, True, False))
sys.exit(1 if check.failed else 0)
