"""Checks the bookshop's rules of 28 October 2026 on the books its notes named: whole author names, books about a person,
titles with no author, first editions and later printings, one signature reminder, partial sets, and an author the notes
identify; and the follow-up fixes of 29 October (given and middle names on every path, signed, first-edition and set wordings). Small made-up sales; no shop data, no Claude calls.
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
check('with the set note, from sets of its own edition (the $800 set of first editions needs a sign of one on the copy)',
      [c for c in a['checks'] if 'partial set' in c], ['Complete sets sold for a median of $400; a partial set is worth much less'])

# whole names on the title path, middle names and initials (made-up authors and titles)
names = refdata.Reference(price_files=[sheet('names.csv', ['Title', 'Sale Price', 'Sale Date'], [
    ['The Quiet Orchard by Jane Smith, 1950 first edition', '$300.00', 'Jun 1, 2025'],
    ['The Quiet Orchard, John Smith, 1950', '$80.00', 'Jul 1, 2025'],
    ['The Quiet Orchard, Smith, 1951, cloth', '$60.00', 'Aug 1, 2025'],
    ['Lanterns of the Bayou, Tennessee Williams, 1958', '$400.00', 'Mar 1, 2025'],
    ['Letters Home by John Paul Adams, 1940', '$500.00', 'Jan 1, 2025'],
    ['Letters Home by John Quincy Adams, 1940', '$90.00', 'Feb 1, 2025'],
    ['Letters Home by John Q. Adams 1941', '$70.00', 'Mar 1, 2025'],
    ['Harbor Songs, Christopher Tolkien, 1960', '$150.00', 'Apr 1, 2025'],
    ['Harbor Songs, J.R.R. Tolkien, 1960', '$95.00', 'May 1, 2025'],
    ['Night Garden by Steven Kingsford, 1977 first edition', '$170.00', 'Jun 1, 2025']])])
hits = names.comps('John Smith', 'The Quiet Orchard')[0]
check("John Smith's copy: no value from Jane Smith's same-title sale, but the surname alone still counts",
      sorted(h['price'] for h in hits), [60.0, 80.0])
check("T. Harry Williams's copy: no value from Tennessee Williams's same-title sale", names.comps('T. Harry Williams', 'Lanterns of the Bayou')[0], [])
check('John Quincy Adams: not John Paul Adams, but John Q. Adams', sorted(h['price'] for h in names.comps('John Quincy Adams', 'Letters Home')[0]), [70.0, 90.0])
check('J. R. R. Tolkien: not Christopher Tolkien', [h['price'] for h in names.comps('J. R. R. Tolkien', 'Harbor Songs')[0]], [95.0])
check('nor in the author-wide sales', [h['price'] for h in names.author_comps('John Quincy Adams')], [90.0, 70.0])
check("a seller's spelling still counts (Steven for Stephen)", [h['price'] for h in names.comps('Stephen Kingsford', 'Night Garden')[0]], [170.0])
check('C. S. Lewis is not Sinclair Lewis', names.names_fit('C. S. Lewis', 'mere words, sinclair lewis, 1930'), False)
check('a title word before the surname is no given name ("Emma Austen")', names.names_fit('Jane Austen', 'emma austen 1816', 'Emma'), True)

# signed wording, first-edition wording and set listings
check('"with Signature" is a signed sale', rules.is_signed({'title': 'Some Novel 1953 First Print with Signature'}))
check('"Single Signature" (a binding term) is not', rules.is_signed({'title': 'Some Novel 1957 First State Single Signature'}), False)
check('first-edition wordings', [bool(rules.FIRST_EDITION_SALE.search(t)) for t in
      ('First Retail Edition, First Print', 'First UK Retail Edition', '1st Print 1984', 'First US Printing', 'Second Printing 1960')],
      [True, True, True, True, False])
retail = refdata.Reference(price_files=[sheet('retail.csv', ['Title', 'Sale Price', 'Sale Date'], [
    ['Glass Harbor, Ray Bradbury, 1953 First Retail Edition, First Print', '$700.00', 'Jan 1, 2025'],
    ['Glass Harbor, Ray Bradbury, 1960s printing', '$40.00', 'Feb 1, 2025']])])
a = rules.assess(book('Glass Harbor', 'Ray Bradbury', look='old'), 'cheap', retail, 30)
check('a $700 "First Retail Edition" sale is no value for an unmarked copy', a['value'], 40.0)
check('set wordings', [bool(rules.SET_LISTING.search(t)) for t in
      ('The Long War in 6 first edition volumes', 'Border Trilogy, first editions', 'Franklin Library Signed First Editions', 'Some Novel, first edition',
       'Some Novel, First Editions Library facsimile')],
      [True, True, False, False, False])
sets = refdata.Reference(price_files=[sheet('sets.csv', ['Title', 'Sale Price', 'Sale Date'], [
    ['A Set of Nine, Joseph Conrad, 1908 first edition', '$300.00', 'Jan 1, 2025']])])
a = rules.assess(book('A Set of Nine', 'Joseph Conrad', look='antique'), 'rare', sets, 75)
check('a title with "set" in it is one book, not a partial set', (a['value'], [c for c in a['checks'] if 'partial set' in c]), (300.0, []))

multi = refdata.Reference(price_files=[sheet('multi.csv', ['Title', 'Sale Price', 'Sale Date'], [
    ['The Long Winter War, Hugh Marsh, Complete First Edition 1931-1936', '$400.00', 'Jan 1, 2025'],
    ['The Long Winter War, Hugh Marsh, volume 2, first edition 1932', '$40.00', 'Feb 1, 2025']])])
war = dict(look='old', notes={'first': 'Four vols, London: Harrap, 1931-36.', 'confidence': 'high'})
a = rules.assess(book('The Long Winter War', 'Hugh Marsh', **war), 'cheap', multi, 30)
check('a work first published in several volumes: "Complete ... 1931-1936" is a set', a['value'], 40.0)
check('a range alone dates one book otherwise', bool(rules.SET_LISTING.search('The Hobbit, first edition 1937-1966')), False)
check('"Harpers" on the spine is Harper & Brothers in the notes', rules.same_imprint('Harpers', {'first': '1904, Harper & Brothers, London'}), True)

# specialty publishers: a Modern Library or Folio copy is valued only from its publisher's sales, an ordinary copy from neither
houses = refdata.Reference(price_files=[sheet('houses.csv', ['Title', 'Sale Price', 'Sale Date', 'Publisher'], [
    ['Quiet Meridian, Ada Wexford, Modern Library 1950', '$40.00', 'Jan 1, 2025', ''],
    ['Quiet Meridian, Ada Wexford, Modern Library', '$50.00', 'Feb 1, 2025', ''],
    ['Quiet Meridian by Ada Wexford, illustrated, slipcase', '$150.00', 'Mar 1, 2025', 'Folio Society'],
    ['Quiet Meridian, Ada Wexford, 1951 Knopf', '$90.00', 'Apr 1, 2025', ''],
    ['Quiet Meridian, Ada Wexford, Knopf 1952', '$110.00', 'May 1, 2025', ''],
    ['Long Thaw, Ada Wexford, Modern Library 1st edition 1934', '$75.00', 'Jun 1, 2025', '']])])
a = rules.assess(book('Quiet Meridian', 'Ada Wexford', imprint='Modern Library', look='old'), 'cheap', houses, 30)
check('a Modern Library copy: only Modern Library sales', a['value'], 45.0)
a = rules.assess(book('Quiet Meridian', 'Ada Wexford', imprint='?', look='old', foot={'foot_text': 'GIANT / G 41', 'imprint': 'Giant'}), 'cheap', houses, 30)
check('a Modern Library Giant known by its foot: the same', a['value'], 45.0)
a = rules.assess(book('Quiet Meridian', 'Ada Wexford', imprint='', look='old', foot={'logo': 'gold circular emblem', 'imprint': 'Easton Press'}), 'cheap', houses, 30)
check("a foot's guess from an emblem, with no publisher read as text, doesn't make it a publisher's copy", a['value'], 100.0)
a = rules.assess(book('Quiet Meridian', 'Ada Wexford', imprint='The Folio Society', look='modern'), 'cheap', houses, 30)
check('a Folio copy: its Folio sale, which the Publisher column names', a['value'], 150.0)
a = rules.assess(book('Quiet Meridian', 'Ada Wexford', imprint='Knopf', look='old'), 'cheap', houses, 30)
check('an ordinary copy: neither publisher\'s sales', a['value'], 100.0)
a = rules.assess(book('Long Thaw', 'Ada Wexford', imprint='Modern Library', look='old', notes={'first': '1921, Harrap, London', 'confidence': 'high'}), 'cheap', houses, 30)
check("a Modern Library \"1st edition\" is Modern Library's first printing, not the work's first from another publisher", a['value'], 75.0)

# a book first published well after the latest year in its author's sales: no value from the author's other books
late = refdata.Reference(price_files=[sheet('late.csv', ['Title', 'Sale Price', 'Sale Date'], [
    ['Copper Moon by Hollis Prentiss, 1895 first edition', '$200.00', 'Jan 1, 2025'], ['Glass Tide, Hollis Prentiss, 1902', '$180.00', 'Feb 1, 2025'],
    ['Hollis Prentiss, The Iron Orchard, 1910', '$220.00', 'Mar 1, 2025']])])
def prentiss(first): return book('Late Harvest', 'Hollis Prentiss', notes={'first': f'{first}, Harrap, London', 'confidence': 'high'})
check('off unless rules.json sets late_book_years', (late.rules.get('late_book_years'), rules.assess(prentiss(1933), 'extreme', late, 0)['basis']),
      (None, "this author's other books (a rough guide)"))
late.rules['late_book_years'] = 10
a = rules.assess(prentiss(1933), 'extreme', late, 0)
check('on: a book first published 23 years after the latest year in the sales gets no rough value', (a['value'], a['decision']), (None, 'skip'))
check('and says why', any('23 years after the latest year' in x for x in a['signals']))
check('one within the years still does', rules.assess(prentiss(1915), 'extreme', late, 0)['basis'], "this author's other books (a rough guide)")

# a book about someone gets no author-wide value on extremely rare and signed shelves
about = refdata.Reference(price_files=[sheet('about.csv', ['Title', 'Sale Price', 'Sale Date'], [
    ['Winter Novel by Mark Schorer, 1950', '$200.00', 'Jan 1, 2025'], ['Spring Stories by Mark Schorer, 1952', '$220.00', 'Feb 1, 2025'],
    ['Autumn Essays by Mark Schorer, 1955', '$240.00', 'Mar 1, 2025'], ['Summer Poems, Mark Schorer, signed, 1956', '$600.00', 'Apr 1, 2025']])])
a = rules.assess(book('Sinclair Lewis', 'Mark Schorer', notes={'about': 'Sinclair Lewis', 'confidence': 'high'}), 'extreme', about, 0)
check("about someone, on an extremely rare shelf: no rough value from the author's other books", (a['value'], a['basis']), (None, ''))
a = rules.assess(book('Sinclair Lewis', 'Mark Schorer', notes={'about': 'Sinclair Lewis', 'confidence': 'high'}), 'signed', about, 0)
check("nor, on a signed shelf, from the author's other signed books", (a['value'], a['basis']), (None, ''))
a = rules.assess(book('Harvest Moon', 'Mark Schorer'), 'extreme', about, 0)
check('a book of his own still gets the rough value', a['basis'], "this author's other books (a rough guide)")

# a title on its own: two sales must share a word besides the title's, and one sale won't value a trimmed title
alone = refdata.Reference(price_files=[sheet('alone.csv', ['Title', 'Sale Price', 'Sale Date'], [
    ['Lighthouse Keepers, Hollis Prentiss, 1931', '$150.00', 'Jan 1, 1965'],
    ['Lighthouse Keepers of Maine, Ada Wexford, 1902', '$150.00', 'Feb 1, 1966'],
    ['Copper Weathervanes, Orrin Blake, 1910', '$120.00', 'Jan 1, 1966'],
    ['Copper Weathervanes by Orrin Blake, 1910', '$140.00', 'Feb 1, 1966'],
    ['The Velvet Orchard, 1888', '$90.00', 'Mar 1, 1966']])])
check('two unrelated sales of a short title found on its own: nothing', alone.comps('', 'Lighthouse Keepers', old=True)[0], [])
check('two sales naming the same author: found', len(alone.comps('', 'Copper Weathervanes', old=True)[0]), 2)
check("one sale won't value a trimmed title (The Velvet Orchard Stories, as The Velvet Orchard)", alone.comps('', 'The Velvet Orchard Stories', old=True)[0], [])
check('but does value the whole title', len(alone.comps('', 'The Velvet Orchard', old=True)[0]), 1)

# the notes say nothing about signed copies
a = rules.assess(book('Fahrenheit 451', 'Ray Bradbury', look='modern', notes={'first': '1953, Ballantine; also 200 signed copies in asbestos.',
                 'points': 'Price $1.50 on the jacket. A limited issue was signed by the author.', 'confidence': 'high'}), 'cheap', ref, 50)
check('notes: sentences about signed copies left out', (a['notes']['first'], a['notes']['points']), ('1953, Ballantine;', 'Price $1.50 on the jacket.'))
sys.exit(1 if check.failed else 0)
