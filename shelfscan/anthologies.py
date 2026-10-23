"""Story collections, anthologies and omnibuses (the SME's rule, 23 October 2026): most aren't worth pulling, so they are
treated like paperbacks and left out unless their title is on the whitelist.

A book counts as one when the notes call classifies it so (the notes, from Opus with the book in front of it, have the
last word either way), when its title or spine says so ("stories", "anthology", "omnibus", "five novels"...), or, failing
the notes, when its title is a story collection or anthology found in the price file (reference/collections.json,
classified once by Sonnet from listing titles: about nine in ten right, so it never decides alone while the notes can). The whitelist is the defaults in rules.json plus the answers on the Collections page
(reference/collection_whitelist.json). Poetry and essay collections are listed there too but not covered by the rule yet."""
import re, threading, time
from .refdata import surnames
from .store import Conflict, NotFound

KINDS = ('story', 'anthology', 'omnibus')
KIND_NAMES = {'story': 'story collection', 'anthology': 'anthology', 'omnibus': 'omnibus', 'poetry': 'poetry collection', 'essays': 'essays'}
FORMS = {'story collection': 'story', 'anthology': 'anthology', 'omnibus': 'omnibus'}       # the notes' form -> kind
CUE = re.compile(r"\b(short stories|stories|anthology|omnibus|novellas|novelettes|novels|and other tales|collected (tales|fiction)|"
                 r"complete (tales|short fiction)|the best of)\b", re.I)
CATALOG_KEY, WHITELIST_KEY = 'reference/collections.json', 'reference/collection_whitelist.json'
TTL = 60


def norm(t):
    """'The Bachman Books: Four Early Novels' -> 'bachman books four early novels'."""
    t = re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', (t or '').lower().replace('&', ' and '))
    return re.sub(r'^(the|a|an) ', '', re.sub(r'\s+', ' ', re.sub(r"[^a-z0-9 ]", ' ', t)).strip())


def main_title(t):
    return norm(re.split(r'\s*[:;]|\s+-\s+', t or '')[0])


def key(work, author):
    s = surnames(author)
    return f'{main_title(work)}|{s[0] if s else ""}'


def _same_author(a, b):
    sa, sb = set(surnames(a)), set(surnames(b))
    return not sa or not sb or bool(sa & sb)


class Collections:
    """The catalog and whitelist, read from the store and kept for TTL seconds."""
    def __init__(self, store, defaults):
        self.store, self.defaults = store, list(defaults or [])
        self.lock, self.at, self._catalog, self._answers = threading.Lock(), 0, [], {}

    def _load(self):
        with self.lock:
            if time.time() - self.at < TTL: return
            self._catalog = self._answers = None
            for k in (CATALOG_KEY, WHITELIST_KEY):
                try: v = self.store.get_json(k)[0] if self.store else {}
                except (NotFound, OSError, ValueError): v = {}
                if k == CATALOG_KEY: self._catalog = v.get('works') or []
                else: self._answers = v
            self.at = time.time()

    def invalidate(self):
        with self.lock: self.at = 0

    def catalog(self):
        self._load()
        return self._catalog

    def answers(self):
        """{'decisions': {key: {decision, note, at, work, author}}, 'added': [{id, work, author, note, at}]}."""
        self._load()
        return {'decisions': dict(self._answers.get('decisions') or {}), 'added': list(self._answers.get('added') or [])}

    def whitelist(self):
        """[{work, author, source}]: the rules.json defaults, the catalog works answered yes, and titles added by hand."""
        a = self.answers()
        out = [dict(e, source='default') for e in self.defaults]
        out += [dict(work=d['work'], author=d.get('author', ''), source='answered yes') for d in a['decisions'].values() if d.get('decision') == 'yes']
        out += [dict(work=e['work'], author=e.get('author', ''), source='added') for e in a['added']]
        return out

    def kind(self, b):
        """(kind, how it was recognised) for a story collection, anthology or omnibus; (None, None) otherwise."""
        title, author = b.get('title') or '', b.get('author') or ''
        form = (b.get('form') or '').lower()
        if form: return (FORMS[form], 'the notes') if form in FORMS else (None, None)
        m = CUE.search(' '.join([title, b.get('spine_text') or '']))
        if m: return ('omnibus' if 'novel' in m.group(0).lower() else 'story'), f'"{m.group(0).lower()}" in the title'
        t = main_title(title)
        if len(t) >= 4 and b.get('notes_tried'):            # only once the notes have had their say and given no form
            for w in self.catalog():
                if w['kind'] in ('story', 'anthology') and main_title(w['work']) == t and _same_author(w.get('author'), author):
                    return w['kind'], 'a collection in the price file'
        return None, None

    def whitelisted(self, b):
        """The whitelist entry that covers this book's title, or None."""
        t, author = norm(b.get('title') or ''), b.get('author') or ''
        for e in self.whitelist():
            w = main_title(e['work'])
            if w and w in t and _same_author(e.get('author'), author): return e
        return None

    def answer(self, k, work, author, decision=None, note=None, now=None, retries=8):
        """Records 'yes' or 'no' (or '' to unanswer) and/or a note for one catalog work; None leaves either as it is."""
        for _ in range(retries):
            try: doc, etag = self.store.get_json(WHITELIST_KEY)
            except NotFound: doc, etag = {}, None
            ds = doc.setdefault('decisions', {})
            cur = ds.get(k) or {}
            d = cur.get('decision', '') if decision is None else decision
            n = cur.get('note', '') if note is None else str(note).strip()[:500]
            if d or n: ds[k] = dict(work=work, author=author, decision=d, note=n, at=now or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            else: ds.pop(k, None)
            try:
                if etag is None: self.store.put_json(WHITELIST_KEY, doc, if_none_match=True)
                else: self.store.put_json(WHITELIST_KEY, doc, if_match=etag)
            except Conflict: continue
            self.invalidate()
            return ds.get(k) or {'decision': '', 'note': ''}
        raise Conflict(WHITELIST_KEY)

    def add(self, work, author, note='', remove_id=None, retries=8):
        """Adds a title to the whitelist by hand, or removes the added title with remove_id."""
        import secrets
        for _ in range(retries):
            try: doc, etag = self.store.get_json(WHITELIST_KEY)
            except NotFound: doc, etag = {}, None
            added = doc.setdefault('added', [])
            if remove_id: doc['added'] = [e for e in added if e.get('id') != remove_id]
            elif work.strip(): added.append(dict(id=secrets.token_hex(4), work=work.strip()[:200], author=author.strip()[:120],
                                                 note=note.strip()[:500], at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
            try:
                if etag is None: self.store.put_json(WHITELIST_KEY, doc, if_none_match=True)
                else: self.store.put_json(WHITELIST_KEY, doc, if_match=etag)
            except Conflict: continue
            self.invalidate()
            return doc['added']
        raise Conflict(WHITELIST_KEY)
