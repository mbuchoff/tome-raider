"""Checks how sold listings load: a sale an earlier file already holds is kept once (with what the later file adds, and
never across different eBay item numbers), the conditions rules.json leaves out are dropped, a Signed column marks signed
sales, Easton Press and Franklin Library sales value only their own copies, and the word index finds exactly what a full
scan finds. Small made-up files; no shop data, no Claude calls.
usage: python tests/prices_check.py"""
import csv, json, os, sys, tempfile
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

core = sheet('core.csv', ['Title', 'Sale Price', 'Sale Date'], [
    ['Wuthering Heights Emily Brontë 1st Edition 1847', '$900.00', 'Mar 2, 2024'],
    ['Flannery O\'Connor Wise Blood First Edition', '$450.00', 'Jan 5, 2024'],
    ['Flannery O\'Connor Wise Blood First Edition', '$450.00', 'Jan 5, 2024'],           # the same sale twice in one file: both kept
    ['Daphne du Maurier Don\'t Look Now first edition', '$120.00', 'Feb 1, 2025'],
    ['Fahrenheit 451 Ray Bradbury Easton Press', '$300.00', 'Jan 1, 2025'],                   # the Easton file says signed and sealed
    ['Fahrenheit 451 Ray Bradbury Ballantine first edition 1953', '$900.00', 'Mar 3, 2025'],
    ['INVISIBLE SCARLET PEOPLE first edition', '$80.00', 'Jul 7, 2025'],
    ['American Psycho Bret Easton Ellis first edition', '$200.00', 'Aug 8, 2025'],
])
firsts = sheet('firsts.csv', ['Image Link', 'Title', 'Sold Price', 'Sale Date'], [
    ['https://i.ebayimg.com/x/1.JPG', 'Flannery O\'Connor Wise Blood First Edition', '$450.00', 'Jan 5, 2024'],     # already in core
    ['https://i.ebayimg.com/x/2.JPG', 'Tim O\'Brien The Things They Carried 1st Edition', '$300.00', 'Jun 9, 2025'],
])
easton = sheet('easton.csv', ['Image Link', 'Title', 'Condition', 'Signed', 'Publisher', 'Sold Price', 'Sale Date'], [
    ['https://i.ebayimg.com/x/3.JPG', 'Fahrenheit 451 Ray Bradbury Easton Press', 'Used', 'No', 'Easton Press', '$125.00', 'Apr 24, 2026'],
    ['https://i.ebayimg.com/x/4.JPG', 'Fahrenheit 451 Ray Bradbury Easton Press Collector Edition', 'Used', 'Yes', 'Easton Press', '$300.00', 'Aug 12, 2024'],
    ['https://i.ebayimg.com/x/5.JPG', 'Fahrenheit 451 Ray Bradbury Easton Press', 'Sealed', 'No', 'Easton Press', '$400.00', 'May 1, 2025'],
    ['https://i.ebayimg.com/x/6.JPG', 'Fahrenheit 451 Ray Bradbury Easton Press', 'New', 'No', 'Easton Press', '$350.00', 'May 2, 2025'],
    ['https://i.ebayimg.com/x/7.JPG', 'Fahrenheit 451 Ray Bradbury Easton Press', 'Sealed', 'Yes', 'Easton Press', '$300.00', 'Jan 1, 2025'],
])
listed = sheet('listed.csv', ['Title', 'ebay link', 'Price', 'sell Date'], [
    ['Charlotte\'s Web E.B. White first edition', 'https://www.ebay.com/itm/111111111111', '$500.00', '04/01/2024'],
])
relisted = sheet('relisted.csv', ['Title', 'ebay link', 'Price', 'sell Date'], [
    ['Charlotte\'s Web E.B. White first edition', 'https://www.ebay.com/itm/222222222222', '$500.00', '04/01/2024'],   # another copy, same day
])
odd = sheet('odd.csv', ['Title', 'Sold Price', 'Sale Date'], [['invısıble scarlet people 1st', '$95.00', 'Sep 9, 2025']])

rows, version = refdata.load_prices([core, firsts, easton])
check('a sale an earlier file holds loads once', ([f.get('already_loaded') for f in version['files']], len(rows)), ([None, 1, 1], 13))
merged = next(r for r in rows if r['title'] == 'Fahrenheit 451 Ray Bradbury Easton Press' and r['price'] == 300)
check('and takes on what the later file knows', (merged['condition'], merged['signed'], merged['publisher'], merged['image'][-5:]),
      ('Sealed', True, 'Easton Press', '7.JPG'))
rows2, _ = refdata.load_prices([listed, relisted])
check('different eBay item numbers are different sales', len(rows2), 2)
check('the same sale twice in one file stays twice', sum(1 for r in rows if 'wise blood' in r['text']), 2)
check('image links and Easton columns are read', [(r['image'][-5:], r['condition'], r['signed'], r['publisher']) for r in rows if r['image'][-5:] in ('3.JPG', '4.JPG')],
      [('3.JPG', 'Used', False, 'Easton Press'), ('4.JPG', 'Used', True, 'Easton Press')])

ref = refdata.Reference(price_files=[core, firsts, easton, odd])
check('sealed and new sales are left out, as rules.json says (the merged sealed sale too)',
      ([r['condition'] for r in ref.prices if 'easton press' in r['text']], ref.price_version.get('left_out_by_condition')), (['Used', 'Used'], 3))
hits, _ = ref.comps('Ray Bradbury', 'Fahrenheit 451')
E = 'Easton Press'
kept, only_signed = rules.relevant_comps(hits, 'hardcover', signed_ok=False, house=E)
check('an Easton copy: a sale the Signed column marks is left out when unsigned', [h['price'] for h in kept], [125.0])
check('and counts when signed', sorted(h['price'] for h in rules.relevant_comps(hits, 'hardcover', signed_ok=True, house=E)[0]), [125.0, 300.0])
check('an ordinary copy is valued from no Easton sale', [h['price'] for h in rules.relevant_comps(hits, 'hardcover', signed_ok=False)[0]], [900.0])
check('and the author\'s Easton sales count only for Easton copies', ([h['price'] for h in rules.same_house(ref.signed_comps('Ray Bradbury'), E)],
      [h['price'] for h in rules.same_house(ref.author_comps('Ray Bradbury'), E)], [h['price'] for h in rules.same_house(ref.author_comps('Ray Bradbury'), None)]),
      ([300.0], [125.0], [900.0]))
check('whose copy is it', [rules.special_house(t) for t in ('EASTON PRESS', 'The Franklin Library', 'Bret Easton Ellis', 'Benjamin Franklin', 'Knopf')],
      ['Easton Press', 'Franklin Library', None, None, None])
check('the specialty publishers too, a Modern Library Giant by its number, and Easton\'s Kelmscott edition is Easton\'s',
      [rules.special_house(t) for t in ('Modern Library', 'GIANT / G 26', 'The Folio Society', 'Heritage Club', 'ARKHAM HOUSE',
                                        'Easton Press Kelmscott Edition', 'Gnome', 'a giant of a book')],
      ['Modern Library', 'Modern Library', 'Folio Society', 'Heritage Press', 'Arkham House', 'Easton Press', None, None])

# a Special Editions file: exported from Excel on Windows (cp1252), its Publisher column naming each sale's publisher
special = os.path.join(d, 'special.csv')
with open(special, 'w', newline='', encoding='cp1252') as f:
    csv.writer(f).writerows([['Image Link', 'Title', 'Sold Price', 'Format', 'Sale Date', 'Publisher'],
                             ['', 'Night Harbor \u2013 illustrated, slipcase', '$140.00', 'Hardcover', 'Jan 17, 2026', 'Folio Society']])
rows3, _ = refdata.load_prices([special])
check('a cp1252 file loads, its dash intact', [(r['title'], r['publisher']) for r in rows3], [('Night Harbor \u2013 illustrated, slipcase', 'Folio Society')])
check('and its publisher column says whose sale it is, though the title doesn\'t', rules.house_of_sale(rows3[0]), 'Folio Society')

# a supplementary file (Specialty Genres) counts only for a title or author the main files have no sales of
genres = os.path.join(d, 'specialty_genres.csv')
with open(genres, 'w', newline='') as f:
    csv.writer(f).writerows([['Title', 'Sold Price', 'Sale Date', 'Category'],
                             ['Wuthering Heights, Emily Brontë, 1850 leather', '$4,000.00', 'Feb 2, 2024', 'Antiquarian'],
                             ['The Hollow Lantern by Orrin Vale, occult grimoire, numbered', '$180.00', 'Mar 3, 2025', 'Occult']])
both = refdata.Reference(price_files=[core, genres])
check('a title the main files have: their sales only', [h['price'] for h in both.comps('Emily Brontë', 'Wuthering Heights')[0]], [900.0])
check('a title only the supplementary file has: its sales', [h['price'] for h in both.comps('Orrin Vale', 'The Hollow Lantern')[0]], [180.0])
check('and an author only it has', [h['price'] for h in both.author_sales('Orrin Vale')], [180.0])

# the index finds what a full scan finds, apostrophes and accents included
queries = [('Emily Brontë', 'Wuthering Heights'), ("Flannery O'Connor", 'Wise Blood'), ("Tim O'Brien", 'The Things They Carried'),
           ('Daphne du Maurier', "Don't Look Now"), ('', "Don't Look Now"), ('', 'The Things They Carried'), ('', 'Wuthering Heights'),
           ('Ray Bradbury', 'Fahrenheit 451'), ('Nobody Atall', 'Nothing Here'), ('', ''), ('', 'Invisible Scarlet People'),
           ('Isaac Asımov', 'Foundation')]
def results():
    return [([r['title'] for r in ref.comps(a, t, old=True)[0]], [r['title'] for r in ref.signed_comps(a)], [r['title'] for r in ref.author_comps(a)])
            for a, t in queries]
indexed = results()
having, ref.having = ref.having, (lambda *texts: ref.prices)
scanned = results()
ref.having = having
check('the index finds exactly what a full scan finds', indexed, scanned)
check('and finds things', sum(1 for x in indexed if x[0]), 9)
check('including a listing spelled with a dotless ı, which the regex takes for i', len(indexed[10][0]), 2)
check('a lookup reads only the sales holding its rarest word', len(ref.having('wuthering')), 1)
sys.exit(1 if check.failed else 0)
