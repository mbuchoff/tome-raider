"""Checks the search for an author the spine doesn't show (the bookshop's note of 29 October 2026, decided 8 November): a
whole title found alone in the price files, then Open Library, then the Internet Archive, and how the rules use what they
find. Small made-up sales and canned catalog answers; no shop data, no network, no Claude calls.
usage: python tests/catalogs_check.py"""
import csv, json, os, sys, tempfile
os.environ['SHELFSCAN_HOME'] = tempfile.mkdtemp()
for k in ('AZURE_STORAGE_CONNECTION_STRING',): os.environ.pop(k, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shelfscan import catalogs, refdata, rules


def check(name, got, want=True):
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, '' if ok else f'got {got!r}, want {want!r}')
    if not ok: check.failed = True
check.failed = False

d = tempfile.mkdtemp()
rows = [                                                                    # made-up sales, worded like listings
    ['The Copper Lantern - J. Marsh - first edition 2019, Gallowglass', '$140.00', 'Jun 1, 2025', ''],
    ['The Copper Lantern 1/300 Gallowglass Press', '$120.00', 'Jun 2, 2025', ''],
    ['Granite Mill Road by Ada Fenn, first edition', '$300.00', 'Jan 1, 2025', ''],
    ['Saltmarsh Kestrel, Gallowglass Press, numbered', '$95.00', 'Feb 1, 2025', ''],
    ['Saltmarsh Kestrel 1st edition Gallowglass', '$90.00', 'Mar 1, 2025', ''],
    ['Winter Orchard, Tom Reeve, first edition', '$200.00', 'Apr 1, 2025', ''],
    ['Winter Orchard by Lena Ostrow, first printing', '$60.00', 'May 1, 2025', ''],
    ['Glass Harbor by Nell Pryce, first edition', '$150.00', 'Jan 3, 2025', ''],
    ['Glass Harbor, Hal Dorn, 1st printing', '$40.00', 'Jan 4, 2025', ''],
    ['Notes on Kestrels and Owls by Ivo Brand', '$90.00', 'Jan 5, 2025', ''],
] + [[f'The Red Barn first edition {i} by Ann Holt', '$80.00', f'Jan {i}, 2024', ''] for i in range(1, 23)]
p = os.path.join(d, 'sales.csv')
with open(p, 'w', newline='') as f: csv.writer(f).writerows([['Title', 'Sale Price', 'Sale Date', 'Author']] + rows)
ref = refdata.Reference(price_files=[p])


def book(title, author='', **kw):
    b = dict(shelf=1, pos=1, x0=0, x1=10, y0=0, y1=100, title=title, author=author, imprint='', format='hardcover', look='modern',
             marks='', spine_text=title.upper(), appearance='', legibility='clear', confidence=0.9)
    b.update(kw)
    return b


# tier 1: the price files, by the whole title alone
check('a two-word title with no author finds the listings it stands alone in', len(ref.comps('', 'The Copper Lantern')[0]), 2)
check('but not one where it begins a longer title ("Granite Mill Road")', ref.comps('', 'Granite Mill')[0], [])
check('a title read only in part finds nothing', ref.comps('', '…The Copper Lantern')[0], [])
check('listings that name different authors find nothing', ref.comps('', 'Winter Orchard')[0], [])
check('nor do two of another title that disagree', ref.comps('', 'Glass Harbor')[0], [])
check('a title more than 20 listings hold is too common to match alone', ref.comps('', 'The Red Barn')[0], [])
check('a publisher the spine shows clearly must be in the listings', ref.comps('', 'Saltmarsh Kestrel', imprint='Oxford')[0], [])
check('one it shows doubtfully ("?") is not held against them', len(ref.comps('', 'Saltmarsh Kestrel', imprint='Oxford?')[0]), 2)
check('and the spine publisher being in them is fine', len(ref.comps('', 'Saltmarsh Kestrel', imprint='Gallowglass Press')[0]), 2)
a = rules.assess(book('The Copper Lantern'), 'rare', ref, 100)
check('so the rules value it from those sales, as its title', (a['decision'], a['value'], a['basis']), ('pull', 130.0, 'sales of this title'))

# which books the catalogs are asked about
check('an authorless hardcover whose title finds no sales is asked about', rules.wants_author(book('Lark Hollow'), ref))
check('not one whose title finds sales', rules.wants_author(book('The Copper Lantern'), ref), False)
check('not one with an author on the spine', rules.wants_author(book('Lark Hollow', 'Nell Pryce'), ref), False)
check('not a paperback', rules.wants_author(book('Lark Hollow', format='paperback'), ref), False)
check('not a partial title', rules.wants_author(book('…Lark Hollow'), ref), False)
check('not one the notes already named an author for', rules.wants_author(book('Lark Hollow', notes={'author': 'Ada Fenn', 'confidence': 'high'}), ref), False)
check('not one already looked up', rules.wants_author(book('Lark Hollow', lookup={'author': '', 'tried': ['Open Library']}), ref), False)

# the catalogs, with canned answers
ANSWERS = {}
asked = []


def fake_get(url, params):
    asked.append((url.split('/')[2], params.get('q')))
    key = (url.split('/')[2], params.get('q'))
    v = ANSWERS.get(key)
    if isinstance(v, Exception): raise v
    return v if v is not None else ({'docs': []} if 'openlibrary' in url else {'response': {'docs': []}})


catalogs._get = fake_get
OL, IA = 'openlibrary.org', 'archive.org'


def ol(*docs): return {'docs': [dict(title=t, author_name=[a], publisher=pubs, first_publish_year=y) for t, a, pubs, y in docs]}
def ia(*docs): return {'response': {'docs': [dict(title=t, creator=c, publisher=pub, date=f'{y}-01-01T00:00:00Z') for t, c, pub, y in docs]}}


ANSWERS[(OL, 'glass harbor')] = ol(('Glass Harbor', 'Nell Pryce', ['Hollis House'], 2001), ('Glass harbor', 'Nell Pryce', [], 2003))
got = catalogs.find(book('Glass Harbor'))
check('Open Library: the same title by a person, most of the records agreeing', (got['author'], got['source']), ('Nell Pryce', 'Open Library'))
check('with the record it came from', (got['title'], got['year']), ('Glass Harbor', 2001))

ANSWERS[(OL, 'lark hollow')] = ol(('Lark Hollow', 'Time-Life Books Inc.', ['Time-Life'], 1975))
ANSWERS[(IA, 'title:("lark hollow")')] = ia(('Lark hollow : a history', 'Fenn, Ada', 'New York : Hollis House', 1974))
got = catalogs.find(book('Lark Hollow'))
check('a publisher as author is passed over, and the Internet Archive asked next; its "Fenn, Ada" read as Ada Fenn',
      (got['author'], got['source']), ('Ada Fenn', 'Internet Archive'))

ANSWERS[(OL, 'tin whistle hollis')] = ol(('Tin Whistle', 'Bea Lowry', ['Penguin'], 1990), ('Tin Whistle', 'Cal Ames', ['Hollis House'], 1988))
got = catalogs.find(book('Tin Whistle', imprint='Hollis House'))
check("the spine's publisher goes in the search and picks between records", got['author'], 'Cal Ames')

ANSWERS[(OL, 'iron gate')] = ol(('Iron Gate', 'Bea Lowry', [], 2006), ('The Iron Gate', 'Cal Ames', ['Hollis House'], 1884))
got = catalogs.find(book('Iron Gate', look='antique'))
check("a record too new for the spine's look is left out: an antique spine, a 2006 book", got['author'], 'Cal Ames')

ANSWERS[(OL, 'bone flute')] = ol(('Bone Flute', 'Bea Lowry', [], 1990), ('Bone Flute', 'Cal Ames', [], 1991))
got = catalogs.find(book('Bone Flute'))
check('records that split between authors give none', (got['author'], got['tried']), ('', ['Open Library', 'Internet Archive']))

ANSWERS[(OL, 'bone flutes')] = ol(('Bone Flutes of the North', 'Bea Lowry', [], 1990))
check('a longer title is a different book', catalogs.find(book('Bone Flutes'))['author'], '')

ANSWERS[(OL, 'salt road')] = OSError('connection reset')
asked.clear()
check('a catalog that fails gives no answer, so a later run asks again', catalogs.find(book('Salt Road')), None)

# the pipeline step: every book that wants one, once; then the rules use it
books = [book('Glass Harbor'), book('The Copper Lantern', shelf=2), book('Zed Quarry', 'Bea Lowry', shelf=3)]
asked.clear()
n = catalogs.add_authors(books, ref)
check('only the authorless book whose title found no sales is looked up', ([q for _, q in asked], n), (['glass harbor'], 1))
check('and what was found is kept on its reading, for replays', books[0]['lookup']['author'], 'Nell Pryce')
asked.clear()
catalogs.add_authors(books, ref)
check('a second pass asks nothing', asked, [])
check('a spine read only as "?" is not looked up', rules.wants_author(book('?'), ref), False)
twins = [book('Glass Harbor', 'Nell Pryce', shelf=1), book('Glass Harbor', '(unreadable)', shelf=1, x0=40)]
asked.clear()
catalogs.add_authors(twins, ref)
check("a copy with no author takes the author of a same-titled book on the photo, and no catalog is asked",
      (twins[1]['lookup'], asked), ({'author': 'Nell Pryce', 'source': 'another copy on this photo'}, []))
twins = [book('The Copper Lantern', 'Ivo Brand'), book('The Copper Lantern', x0=40)]
catalogs.add_authors(twins, ref)
a = rules.assess(twins[1], 'rare', ref, 100)
check('even when its title alone would have found sales, which are another author\'s', (a.get('identified'), a['value']), ('Ivo Brand', None))
a = rules.assess(books[0], 'rare', ref, 100)
check("the rules look it up by that author and value it from that author's sales of it", (a['decision'], a['value']), ('pull', 150.0))
check('and the card says where the author came from', any(s == 'author found in Open Library as Nell Pryce' for s in a['signals']))
a = rules.assess(book('Glass Harbor', notes={'author': 'Tom Reeve', 'confidence': 'high'}, lookup={'author': 'Nell Pryce', 'source': 'Open Library'}), 'rare', ref, 100)
check("the notes' author, when there is one, comes first", a.get('identified'), 'Tom Reeve')
a = rules.assess(book('Owls', lookup={'author': 'Ivo Brand', 'source': 'Open Library'}), 'rare', ref, 100)
check("a found author's sales count only where the title stands alone: not Owls in Notes on Kestrels and Owls", a['value'], None)
a = rules.assess(book('Owls', 'Ivo Brand'), 'rare', ref, 100)
check('an author read on the spine is matched as before', a['value'], 90.0)
sys.exit(1 if check.failed else 0)
