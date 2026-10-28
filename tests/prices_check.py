"""Checks how sold listings load: a sale an earlier file already holds is kept once, the conditions rules.json leaves out
are dropped, a Signed column marks signed sales, and the word index finds exactly what a full scan finds. Small made-up
files; no shop data, no Claude calls.
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
])

rows, version = refdata.load_prices([core, firsts, easton])
check('a sale an earlier file holds loads once', ([f.get('already_loaded') for f in version['files']], len(rows)), ([None, 1, None], 9))
check('the same sale twice in one file stays twice', sum(1 for r in rows if 'wise blood' in r['text']), 2)
check('image links and Easton columns are read', [(r['image'][-5:], r['condition'], r['signed'], r['publisher']) for r in rows if 'fahrenheit' in r['text']][:2],
      [('3.JPG', 'Used', False, 'Easton Press'), ('4.JPG', 'Used', True, 'Easton Press')])

ref = refdata.Reference(price_files=[core, firsts, easton])
check('sealed and new sales are left out, as rules.json says', ([r['condition'] for r in ref.prices if 'fahrenheit' in r['text']],
      ref.price_version.get('left_out_by_condition')), (['Used', 'Used'], 2))
hits, _ = ref.comps('Ray Bradbury', 'Fahrenheit 451')
kept, only_signed = rules.relevant_comps(hits, 'hardcover', signed_ok=False)
check('a sale the Signed column marks is left out for an unsigned copy', [h['price'] for h in kept], [125.0])
check('and counts for a signed one', sorted(h['price'] for h in rules.relevant_comps(hits, 'hardcover', signed_ok=True)[0]), [125.0, 300.0])
check('and as a signed copy of the author', [r['price'] for r in ref.signed_comps('Ray Bradbury')], [300.0])
check('not as an unsigned one', [r['price'] for r in ref.author_comps('Ray Bradbury')], [125.0])

# the index finds what a full scan finds, apostrophes and accents included
queries = [('Emily Brontë', 'Wuthering Heights'), ("Flannery O'Connor", 'Wise Blood'), ("Tim O'Brien", 'The Things They Carried'),
           ('Daphne du Maurier', "Don't Look Now"), ('', "Don't Look Now"), ('', 'The Things They Carried'), ('', 'Wuthering Heights'),
           ('Ray Bradbury', 'Fahrenheit 451'), ('Nobody Atall', 'Nothing Here'), ('', '')]
def results():
    return [([r['title'] for r in ref.comps(a, t, old=True)[0]], [r['title'] for r in ref.signed_comps(a)], [r['title'] for r in ref.author_comps(a)])
            for a, t in queries]
indexed = results()
having, ref.having = ref.having, (lambda *texts: ref.prices)
scanned = results()
ref.having = having
check('the index finds exactly what a full scan finds', indexed, scanned)
check('and finds things', sum(1 for x in indexed if x[0]), 8)
check('a lookup reads only the sales holding its rarest word', len(ref.having('wuthering')), 1)
sys.exit(1 if check.failed else 0)
