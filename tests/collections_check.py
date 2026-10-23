"""Checks the collections rule: story collections, anthologies and omnibuses are left out unless whitelisted, with the
Collections page's answers, notes and hand-added titles. Folder store, no Claude calls.
usage: python tests/collections_check.py"""
import os, sys, tempfile
HOME = tempfile.mkdtemp()
os.environ.update(SHELFSCAN_HOME=HOME, SHELFSCAN_PASSCODE='x', SHELFSCAN_INSECURE_COOKIE='1', ANTHROPIC_API_KEY='unused')
for k in ('AZURE_STORAGE_CONNECTION_STRING', 'ANTHROPIC_ADMIN_KEY'): os.environ.pop(k, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app as web
from shelfscan import anthologies, pipeline, rules

store, c = web.store, web.app.test_client()
store.put_json(anthologies.CATALOG_KEY, {'works': [
    dict(work='Long After Midnight', author='Ray Bradbury', kind='story', sales=2, median=180, max=200, example='x'),
    dict(work='Another Country', author='James Baldwin', kind='omnibus', sales=1, median=148, max=148, example='x'),
    dict(work='Howl and Other Poems', author='Allen Ginsberg', kind='poetry', sales=1, median=900, max=900, example='x')]})
ref = pipeline.reference(); col = ref.collections; col.invalidate()


def check(name, got, want):
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, got, '' if ok else f'(wanted {want})')
    if not ok: check.failed = True


def book(title, author, **kw):
    return dict(dict(title=title, author=author, spine_text=f'{title} / {author}', imprint='', format='hardcover', look='modern', marks='',
                     appearance='', legibility='clear', shelf=1, pos=1, of=1, x0=0, x1=10, y0=0, y1=100, confidence=0.9), **kw)


check.failed = False
check('title says stories', col.kind(book('The Complete Stories', 'Franz Kafka'))[0], 'story')
check('title says novels', col.kind(book('Five Novels of the 1960s & 70s', 'Philip K. Dick'))[0], 'omnibus')
check('a novel is not', col.kind(book('The Man in the High Castle', 'Philip K. Dick'))[0], None)
check('price-file match waits for the notes', col.kind(book('Long After Midnight', 'Ray Bradbury'))[0], None)
check('then decides if the notes give no form', col.kind(book('Long After Midnight', 'Ray Bradbury', notes_tried=True))[0], 'story')
check('the notes have the last word', col.kind(book('Long After Midnight', 'Ray Bradbury', notes_tried=True, form='novel'))[0], None)
check('notes can find one', col.kind(book('The Wind\'s Twelve Quarters', 'Ursula K. Le Guin', form='story collection'))[0], 'story')
check('price-file omnibus labels are not used', col.kind(book('Another Country', 'James Baldwin', notes_tried=True))[0], None)
check('built-in whitelist: The Bachman Books', bool(col.whitelisted(book('The Bachman Books: Four Early Novels', 'Stephen King'))), True)
check('author must agree', bool(col.whitelisted(book('The Bachman Books', 'Somebody Else'))), False)

kafka = book('The Complete Stories', 'Franz Kafka')
a = rules.assess(kafka, 'extreme', ref, 100)
check('left out like a paperback', (a['decision'], a['reason']), ('skip', 'collection or anthology not on the whitelist'))
a = rules.assess(book('Fairy Tales and Legends', 'Hans Andersen', form='story collection', spine_text='Fairy Tales / Illustrated by Rex Whistler'), 'rare', ref, 100)
check('illustrated editions are kept', a['reason'] != 'collection or anthology not on the whitelist', True)
a = rules.assess(book('The Bachman Books: Four Early Novels', 'Stephen King'), 'extreme', ref, 100)
check('whitelisted one goes on', ('omnibus on the whitelist' in a['signals'], a['decision']), (True, 'pull'))

c.post('/login', data={'passcode': 'x'})
page = c.get('/collections').data.decode()
check('page lists the covered works', 'Long After Midnight' in page and 'Found in the sales data (2)' in page, True)
check('poetry listed apart', 'Poetry and essay collections (1)' in page, True)
k = anthologies.key('The Complete Stories', 'Franz Kafka')
r = c.post('/collections/answer', json={'key': k, 'work': 'The Complete Stories', 'author': 'Franz Kafka', 'decision': 'yes'}).get_json()
check('answer yes', (r['decision'], r['whitelisted']), ('yes', 2))
check('now kept', rules.assess(kafka, 'extreme', ref, 100)['reason'] != 'collection or anthology not on the whitelist', True)
r = c.post('/collections/answer', json={'key': k, 'work': 'The Complete Stories', 'author': 'Franz Kafka', 'note': ' only the Schocken first '}).get_json()
check('a note keeps the answer', (r['decision'], r['note']), ('yes', 'only the Schocken first'))
r = c.post('/collections/answer', json={'key': k, 'work': 'The Complete Stories', 'author': 'Franz Kafka', 'decision': ''}).get_json()
check('clear keeps the note', (r['decision'], r['note'], r['whitelisted']), ('', 'only the Schocken first', 1))
check('left out again', rules.assess(kafka, 'extreme', ref, 100)['reason'], 'collection or anthology not on the whitelist')
c.post('/collections/add', data={'work': 'Different Seasons', 'author': 'Stephen King', 'note': 'four novellas'})
check('added by hand', bool(col.whitelisted(book('Different Seasons', 'Stephen King', form='story collection'))), True)
added = col.answers()['added']
check('the note is kept', added[0]['note'], 'four novellas')
c.post('/collections/remove', data={'id': added[0]['id']})
check('removed', bool(col.whitelisted(book('Different Seasons', 'Stephen King', form='story collection'))), False)
check('bad answer refused', c.post('/collections/answer', json={'key': k, 'work': 'x', 'decision': 'maybe'}).status_code, 400)
sys.exit(1 if check.failed else 0)
