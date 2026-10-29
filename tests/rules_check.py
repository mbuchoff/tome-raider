"""Checks the bookshop's rules of 28 October 2026 on the books its notes named: whole author names, books about a person,
titles with no author, first editions and later printings, one signature reminder, partial sets, and an author the notes
identify. Small made-up sales; no shop data, no Claude calls.
usage: python tests/rules_check.py"""
import csv, os, sys, tempfile
os.environ['SHELFSCAN_HOME'] = tempfile.mkdtemp()
for k in ('AZURE_STORAGE_CONNECTION_STRING',): os.environ.pop(k, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shelfscan import refdata, rules


def check(name, got, want=True):
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, '' if ok else f'got {got!r}, want {want!r}')
    if not ok: check.failed = True
check.failed = False

d = tempfile.mkdtemp()
def sheet(name, head, rows):
    p = os.path.join(d, name)
    with open(p, 'w', newline='') as f: csv.writer(f).writerows([head] + rows)
    return p

core = sheet('core.csv', ['Title', 'Sale Price', 'Sale Date', 'Author'], [          # made-up sales, worded like listings
    ['Peter Rabbit, Beatrix Potter, first edition 1902', '$5,000.00', 'Jun 1, 2026', ''],
    ['Harry Potter Chamber of Secrets, Rowling, first printing', '$3,000.00', 'Jun 2, 2021', ''],
    ['Surgery of the Head and Neck by Levi Cooper Lane, 1896, first edition', '$140.00', 'Nov 1, 2023', ''],
    ['Clint Lane in Korea, Red Reeder, 1961 first edition', '$120.00', 'Aug 1, 2024', ''],
    ['Orthodoxy, G. K. Chesterton, John Lane Company, 1909 first edition', '$90.00', 'Apr 1, 2026', ''],
    ["Sinclair Lewis, It Can't Happen Here, first edition 1935", '$260.00', 'Sep 1, 2024', 'Sinclair Lewis'],
    ['Main Street, Sinclair Lewis, first edition, first printing 1920', '$240.00', 'May 1, 2026', 'Sinclair Lewis'],
    ['Huey Long, My First Days in the White House, 1935 first edition', '$190.00', 'Oct 1, 2024', ''],
    ['My First Days in the White House by Huey Long, 1st ed 1935', '$170.00', 'Oct 2, 2023', ''],
    ["Who's Who in Indian Relics no. 3, first edition", '$110.00', 'Oct 1, 2026', ''],
    ["Who's Who in Indian Relics no. 4, first edition", '$100.00', 'Mar 1, 2024', ''],
    ["Who's Who in Colored America, 1927 first edition", '$180.00', 'Feb 1, 2025', ''],
    ['German Research in World War II, Leslie Simon, 1947 first edition', '$1,000.00', 'May 1, 2021', ''],
    ['Army Air Forces in World War II set, first edition', '$250.00', 'Sep 1, 2024', ''],
    ['30th Infantry Regiment in World War II, 1947 first edition', '$180.00', 'Jul 1, 2021', ''],
    ['The Prophet, Kahlil Gibran, 1923 first edition, Knopf', '$2,500.00', 'Jan 1, 2025', ''],
    ['The Prophet by Kahlil Gibran, first edition 1923', '$1,600.00', 'Feb 1, 2025', ''],
    ['The Prophet, Kahlil Gibran, 1923, first printing', '$1,400.00', 'Mar 1, 2025', ''],
    ['Ulysses, James Joyce, 1922 first edition', '$4,000.00', 'Sep 1, 2024', ''],
    ['Ulysses by James Joyce, first American edition 1934', '$1,800.00', 'Jan 1, 2024', ''],
    ["The Pilgrim's Progress, John Bunyan, 1760, calf", '$500.00', 'Jun 1, 2026', ''],
    ["John Bunyan, The Pilgrim's Progress, 1766", '$200.00', 'Sep 1, 2026', ''],
    ['The Second World War, Winston Churchill, 6 volume set, first editions', '$400.00', 'Sep 1, 2024', ''],
    ['Winston Churchill, The Second World War, all first editions 1948-54', '$800.00', 'Feb 1, 2026', ''],
    ["A Brief History of Children's Literature by Brian Doyle 1968", '$90.00', 'Jan 2, 2025', ''],
    ['The Ring and the Book, Robert Browning, first edition, first printing', '$200.00', 'Aug 1, 2026', ''],
    ['The Iliad, Homer, Franklin Library, leather', '$180.00', 'Feb 1, 2026', ''],
])
easton = sheet('easton.csv', ['Title', 'Condition', 'Signed', 'Publisher', 'Sold Price', 'Sale Date'], [
    ['Fahrenheit 451 Ray Bradbury Easton Press 1990', 'Used', 'No', 'Easton Press', '$120.00', 'Apr 24, 2026'],
    ['Fahrenheit 451 Ray Bradbury Easton Press Collector Edition', 'Used', 'No', 'Easton Press', '$125.00', 'Apr 18, 2024'],
    ['Fahrenheit 451 Ray Bradbury Easton Press', 'Used', 'No', 'Easton Press', '$130.00', 'Mar 3, 2026'],
    ['Fahrenheit 451 by Ray Bradbury SIGNED EDITION Easton Press', 'Used', 'Yes', 'Easton Press', '$300.00', 'Dec 16, 2025'],
    ['Fahrenheit 451 Ray Bradbury Easton Press 1998', 'Used', 'Yes', 'Easton Press', '$330.00', 'Aug 12, 2024'],
    ['Fahrenheit 451 signed by Ray Bradbury Easton Press with COA', 'Like New', 'Yes', 'Easton Press', '$360.00', 'Jun 17, 2026'],
])
ref = refdata.Reference(price_files=[core, easton])


def book(title, author='', **kw):
    b = dict(shelf=1, pos=1, x0=0, x1=10, y0=0, y1=100, title=title, author=author, imprint='', format='hardcover', look='old',
             marks='', spine_text=f'{title} / {author}', appearance='', legibility='clear', confidence=0.9)
    b.update(kw)
    return b

# whole author names
check('"Potter?" alone values nothing by any Potter', ref.author_comps('Potter?'), [])
check('"Charles Lane" finds no Cooper, Clint or John Lane', ref.author_comps('Charles Lane?'), [])
a = rules.assess(book('Istar of Babylon', 'Potter?', look='antique'), 'extreme', ref, 0)
check('Istar of Babylon: no value from Beatrix Potter or Harry Potter', (a['value'], a['basis']), (None, ''))
check('T. Harry Williams is not Tennessee Williams', refdata.same_person('Tennessee Williams', 'T. Harry Williams'), False)

# books about a person
a = rules.assess(book('Sinclair Lewis', 'Mark Schorer', imprint='McGraw-Hill'), 'cheap', ref, 50)
check("Schorer's Sinclair Lewis: not valued from Lewis's novels", (a['decision'] == 'pull', a['value']), (False, None))
check('and says why', any(s.startswith('about Sinclair Lewis') for s in a['signals']))
a = rules.assess(book('Huey Long', 'T. Harry Williams', imprint='Alfred A. Knopf'), 'cheap', ref, 50)
check("without notes, Huey Long's title still finds his memoir (the notes are the catch)", a['value'] is not None)
a = rules.assess(book('Huey Long', 'T. Harry Williams', imprint='Alfred A. Knopf', notes={'about': 'Huey Long', 'confidence': 'high'}), 'cheap', ref, 50)
check('with the notes saying who it is about, no value from his memoir', a['value'], None)

# titles with no author
check("Who's Who in Children's Books finds no Who's Who in Indian Relics", ref.comps('', "Who's Who in Children's Books", old=True)[0], [])
check('World War II finds nothing on its words alone', ref.comps('', 'World War II', old=True)[0], [])
a = rules.assess(book("Who's Who in Children's Books", '', notes={'author': 'Brian Doyle', 'confidence': 'high'}), 'cheap', ref, 50)
check('an author the notes identify is used, and said', (a.get('identified'), any('identified by the notes as Brian Doyle' in s for s in a['signals'])),
      ('Brian Doyle', True))

# first editions and later printings
a = rules.assess(book('The Prophet', 'Kahlil Gibran', imprint='Knopf'), 'cheap', ref, 50)
check('The Prophet, no sign of the 1923 first: not valued from $1,400-$2,500 firsts', (a['value'], a['decision'] == 'pull'), (None, False))
a = rules.assess(book('The Prophet', 'Kahlil Gibran', imprint='Knopf', marks='first edition stated', look='old'), 'cheap', ref, 50)
check('marked first edition on the copy: valued from them', a['value'] is not None and a['value'] >= 1000)
cheap = refdata.Reference(price_files=[sheet('prophet.csv', ['Title', 'Sale Price', 'Sale Date'], [
    ['The Prophet, Kahlil Gibran, 1923 first edition, Knopf', '$2,500.00', 'Jan 1, 2025'],
    ['The Prophet, Kahlil Gibran, first edition 1923 later printing', '$120.00', 'Feb 1, 2025'],
    ['The Prophet, Kahlil Gibran, first edition, 1931 printing', '$90.00', 'Mar 1, 2025'],
    ['The Prophet, Kahlil Gibran, Knopf 1940', '$60.00', 'Apr 1, 2025']])])
a = rules.assess(book('The Prophet', 'Kahlil Gibran', imprint='Knopf'), 'cheap', cheap, 50)
check('each $500+ first-edition sale is left out, even when cheaper "first edition" listings keep the median low',
      ([h['price'] for h in a['comps']], a['value']), ([120.0, 90.0, 60.0], 90.0))
ulysses = dict(imprint='Modern Library', notes={'first': '1922, Shakespeare and Company, Paris; US: Random House, 1934', 'confidence': 'high'})
a = rules.assess(book('Ulysses', 'James Joyce', **ulysses), 'cheap', ref, 50)
check('a Modern Library Ulysses: no value from the 1922 and 1934 firsts', a['value'], None)
a = rules.assess(book("The Pilgrim's Progress", 'John Bunyan', look='old'), 'cheap', ref, 50)
check("an old Pilgrim's Progress: no value from 1760s sales", (a['value'], a['decision'] == 'pull'), (None, False))
a = rules.assess(book("The Pilgrim's Progress", 'John Bunyan', look='antique'), 'rare', ref, 75)
check('an antique copy may be an 18th-century one: valued from those sales', a['value'], 350.0)
a = rules.assess(book("The Pilgrim's Progress", 'John Bunyan', spine_text="The Pilgrim's Progress / John Bunyan / 1950"), 'rare', ref, 75)
check('a quality hardcover dated 1950 is no 1760 edition: a lead to check, said once', (a['decision'], a['value'], sum('check whether this is one' in c for c in a['checks'])),
      ('lead', None, 1))
a = rules.assess(book("The Pilgrim's Progress", 'John Bunyan', look='antique', format='paperback'), 'rare', ref, 75)
check('never a paperback', a['decision'] in ('lead', 'pull'), False)

a = rules.assess(book('The Ring and the Book', 'Browning', imprint='?', notes={'first': '1868-69, Smith, Elder, London, 4 vols.', 'author': 'Robert Browning',
                 'confidence': 'high'}), 'cheap', ref, 50)
check("an undated first edition takes the notes' year: no 1868 value for an old copy", (a['value'], a['decision'] == 'pull'), (None, False))
check('a lone surname is replaced by the author the notes identify', a.get('identified'), 'Robert Browning')
a = rules.assess(book('The Iliad', 'Homer', imprint='Franklin Library', look='modern', notes={'author': 'Richmond Lattimore', 'confidence': 'high'}), 'cheap', ref, 50)
check('but a single-name author like Homer is never replaced by a translator', (a.get('identified'), a['value']), (None, 180.0))

a = rules.assess(book('World War II?', '?', notes={'author': '', 'confidence': 'low'}), 'cheap', ref, 50)
check('an author read as "?" alone is no name, and no crash', a['decision'] in ('skip', 'not pulled'))

# one signature reminder, from the signed sales
a = rules.assess(book('Fahrenheit 451', 'Ray Bradbury', imprint='Easton Press', look='modern'), 'cheap', ref, 50)
check('an Easton copy: valued from unsigned Easton sales', a['value'], 125.0)
check('and one signature line with the signed median', [c for c in a['checks'] if 'signature' in c],
      ['Check for a signature: signed copies sold for a median of $330'])
check('and no generic signature text', [q for q in a['quick'] if 'sign' in q.lower()], [])
a = rules.assess(book('Fahrenheit 451', 'Ray Bradbury', imprint='Easton Press', look='modern', marks='signed'), 'cheap', ref, 50)
check('a copy marked signed gets no reminder', [c for c in a['checks'] if 'signature' in c], [])

# partial sets
a = rules.assess(book('The Second World War', 'Winston S. Churchill', look='old'), 'cheap', ref, 50)
check('two volumes of Churchill: still a pull', a['decision'], 'pull')
check('with the set note', [c for c in a['checks'] if 'partial set' in c], ['Complete sets sold for a median of $600; a partial set is worth much less'])
sys.exit(1 if check.failed else 0)
