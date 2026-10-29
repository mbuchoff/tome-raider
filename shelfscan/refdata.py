"""Reference data: the static price file, frequent and watch-list authors, and the rule lists in rules.json.

The price file is one or more spreadsheets (.xlsx) or CSV files of sold listings. Columns are found by header name,
so a replacement file in the same layout needs no code change. Set SHELFSCAN_PRICE_FILES to a comma-separated list of
paths to use other files.

The shop's sales spreadsheets aren't in the repository: they live in the private storage container under reference/, and
are fetched into a local cache the first time they're needed (a copy in data/ is used instead when present).

A sale found in an earlier file with the same title, price and date is loaded once. Files with a Condition column (Easton
Press and Franklin Library) lose the conditions rules.json leaves out (sold_conditions_left_out); a Signed column marks
signed sales whose listing title doesn't say so. Lookups go through a word index: each one reads only the sales holding its
rarest word, never every sale."""
import collections, csv, datetime, difflib, hashlib, io, json, os, re, statistics
from .xlsx_read import read as read_xlsx

DATA = os.environ.get('SHELFSCAN_DATA') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
DEFAULT_PRICE_FILES = ['sales_report.xlsx', 'master_list_2022.xlsx', 'first_editions.xlsx', 'easton_franklin.xlsx', 'special_editions.csv']


def fetch(names, cache=None):
    """Local paths for these price files: data/<name> if present, else a cached copy of the store's reference/<name>."""
    cache = cache or os.path.join(os.environ.get('SHELFSCAN_HOME') or os.path.join(DATA, '..', 'var'), 'reference')
    out = []
    for n in names:
        p = os.path.join(DATA, n)
        if not os.path.exists(p):
            p = os.path.join(cache, n)
            if not os.path.exists(p):
                from .store import open_store
                os.makedirs(cache, exist_ok=True)
                data = open_store().get_bytes(f'reference/{n}')        # raises NotFound if the store has no copy
                tmp = f'{p}.{os.getpid()}.tmp'
                open(tmp, 'wb').write(data); os.replace(tmp, p)
        out.append(p)
    return out

# header names seen in the provided files, lower case; the first one present wins
COLUMNS = {'title': ['title', 'full title', 'item title'], 'price': ['sale price', 'price', 'sold price'],
           'date': ['sale date', 'sell date', 'sold date', 'date'], 'author': ['author'], 'url': ['ebay link', 'item url', 'url', 'link'],
           'image': ['image link'], 'condition': ['condition'], 'signed': ['signed'], 'publisher': ['publisher']}
YEAR = re.compile(r'(?<!\d)(1[5-9]\d\d|20[0-2]\d)(?!\d)')
STOP = set('the a an of and in on to for with by from at is my your our his her its this that'.split())
# words that don't identify a work on their own: "Selected Poems" or "Fairy Tales" could be anyone's
GENERIC = set('''poems poetry poem stories story tales tale fairy fables works collected selected complete essays letters book books
history novels novel plays verse songs short writings selections anthology treasury nursery rhymes bible prayer hymns dictionary
encyclopedia atlas volume vol edition illustrated new old english american french best great favorite favourite classic classics
legends myths entertainments adventures retold told children's childrens'''.split())


def money(s):
    try: return float(str(s).replace('$', '').replace(',', '').strip())
    except (TypeError, ValueError): return None


def to_date(v):
    if v in (None, ''): return None
    try: return (datetime.date(1899, 12, 30) + datetime.timedelta(days=float(v))).isoformat()
    except (TypeError, ValueError): pass
    for f in ('%m/%d/%Y', '%Y-%m-%d', '%b %d, %Y', '%d %b %Y', '%m/%d/%y'):
        try: return datetime.datetime.strptime(str(v).strip(), f).date().isoformat()
        except ValueError: pass
    return None


def _tables(path):
    if path.lower().endswith('.csv'):
        raw = open(path, 'rb').read()
        try: text = raw.decode('utf-8-sig')
        except UnicodeDecodeError: text = raw.decode('cp1252', errors='replace')     # an export from Excel on Windows
        yield os.path.basename(path), list(csv.reader(io.StringIO(text, newline='')))
    else:
        for name, rows in read_xlsx(path).items(): yield name, rows


def load_prices(paths):
    """Rows of dict(title, author, price, date, year, url, item, image, condition, signed, publisher, text). A repeated eBay
    item number loads once. So does a dated sale an earlier file already holds (same title, price and date, and no
    different item number): what the later file adds (condition, signed, publisher, image) goes onto the first."""
    rows, seen, files, earlier = [], set(), [], {}       # earlier: (title, price, date) -> the row an earlier file gave
    for path in paths:
        files.append({'file': os.path.basename(path), 'sha256': hashlib.sha256(open(path, 'rb').read()).hexdigest()[:12]})
        n0, dupes, mine = len(rows), 0, {}
        for sheet, table in _tables(path):
            if not table: continue
            head = [str(h or '').strip().lower() for h in table[0]]
            col = {k: next((head.index(n) for n in names if n in head), None) for k, names in COLUMNS.items()}
            if col['title'] is None or col['price'] is None: continue
            for r in table[1:]:
                get = lambda k: (r[col[k]] if col[k] is not None and col[k] < len(r) else None)
                title, price = (get('title') or '').strip(), money(get('price'))
                if not title or price is None: continue
                url = (get('url') or '').strip()
                m = re.search(r'/itm/(?:[^/]+/)?(\d{9,})', url)
                item = m.group(1) if m else ''
                if item:
                    if item in seen: continue
                    seen.add(item)
                date = to_date(get('date'))
                image = str(get('image') or '').strip()
                row = dict(title=title, author=(get('author') or '').strip(), price=price, date=date, url=url if url.startswith('http') else '',
                           item=item, image=image if image.startswith('http') else '', condition=str(get('condition') or '').strip(),
                           signed=str(get('signed') or '').strip().lower() in ('yes', 'y', 'true', '1'), publisher=str(get('publisher') or '').strip())
                key = (title.lower(), round(price, 2), date)
                first = earlier.get(key) if date else None          # an undated sale is never taken for another
                if first is not None and not (item and first['item'] and item != first['item']):
                    for k in ('condition', 'publisher', 'image', 'item', 'url', 'author'):      # what this file knows that the first didn't
                        if not first[k]: first[k] = row[k]
                    first['signed'] = first['signed'] or row['signed']
                    dupes += 1; continue
                ys = [int(y) for y in YEAR.findall(title)]
                row.update(year=min(ys) if ys else None, text=(title + ' ' + row['author']).lower())
                rows.append(row)
                mine.setdefault(key, row)
        for k, r in mine.items(): earlier.setdefault(k, r)
        files[-1]['rows'] = len(rows) - n0
        if dupes: files[-1]['already_loaded'] = dupes
    dates = sorted(r['date'] for r in rows if r['date'])
    version = {'files': files, 'rows': len(rows), 'first_sale': dates[0] if dates else None, 'last_sale': dates[-1] if dates else None}
    return rows, version


INITIALS = re.compile(r'(?:[A-Za-z]\.){1,3}|[A-Za-z]\.?')
TITLES = {'sir', 'dr', 'mr', 'mrs', 'ms', 'rev', 'jr', 'sr', 'lord', 'lady', 'dame'}


def name_words(n):
    """'T. Harry Williams?' -> ['T.', 'Harry', 'Williams']; honorifics and question marks dropped."""
    ws = re.sub(r"[^\w\s\.'\-]", ' ', re.sub(r'\(.*?\)', ' ', n or '')).split()
    return [w for w in ws if w.strip('.').lower() not in TITLES and w.strip('.')]


def goes_by(given):
    """The given name an author is known by: the first one spelled out (T. Harry Williams goes by Harry), or None for initials."""
    return next((w for w in given if not INITIALS.fullmatch(w)), None)


def name_rx(n, single_names=()):
    """A pattern for an author's whole name in a listing: the name they go by (or its initial) just before the surname,
    with up to two middle names or initials between, or 'Surname, First'. Initials only (J. R. R. Tolkien) keep the looser
    initial-and-surname match. A surname alone gives None, unless it's a single-name author (Homer, Voltaire): "Potter"
    would otherwise find Beatrix Potter and Harry Potter for any Potter."""
    parts = name_words(n)
    if not parts: return None
    last = re.escape(parts[-1])
    if len(parts) == 1:
        return re.compile(r'\b' + last + r'\b', re.I) if parts[0].lower().strip('.') in single_names else None
    given = parts[:-1]
    name = goes_by(given)
    if name is None:
        f = re.escape(given[0][0])
        return re.compile(r'\b' + f + r'[\w\.]*\.?\s+(?:[\w\.]+\s+){0,2}' + last + r'\b|\b' + last + r',\s*' + f, re.I)
    i = given.index(name)
    before = ''.join(r'(?:' + re.escape(w[0]) + r'\w*\.?\s+)?' for w in given[:i])     # initials before it may or may not be printed
    first = r'(?:' + re.escape(name) + r'|' + re.escape(name[0]) + r'\.)'
    return re.compile(r'\b' + before + first + r'\s+(?:(?:\w\.?|\w+)\s+){0,2}' + last + r'\b|\b' + last + r',\s*' + first, re.I)


# words in a listing that say nothing about which book it is
LISTING_WORDS = set('''first 1st edition editions printing print printed hardcover hardback hc dj hcdj jacket dust cloth binding bound boards
vintage rare scarce antique original early fine good near mint very vg nice excellent condition copy copies book books novel volume
vol vols set signed inscribed autograph illustrated leather gilt press publishing publisher publishers company co inc ltd sons
library club paperback softcover pb new used free shipping stated true author by edition'''.split())


def one_book(hits, phrase):
    """Whether listings found by a title alone are all one book: a word other than the title's own (usually the author's
    name: Alcott for "Little Women") appears in half of them, and in both when there are two. "World War II" or "Who's Who"
    find listings of different books that share no such word. A single listing is taken as it is, and so are two of a title
    with three distinctive words, which is one book on its own."""
    own = set(tokens(phrase))
    if len(hits) < 2 or (len(hits) == 2 and sum(len(w) > 2 and w not in STOP and w not in GENERIC for w in own) >= 3): return True
    counts = collections.Counter()
    for r in hits:
        counts.update({w for w in tokens(r['text']) if len(w) > 2 and not w.isdigit() and w not in own and w not in STOP
                       and w not in GENERIC and w not in LISTING_WORDS})
    top = counts.most_common(1)[0][1] if counts else 0
    return top >= 2 and top * 2 >= len(hits)


def same_person(a, b, single_names=()):
    """Two spellings of one author: the same surname, and the same name they go by (or matching initials where one gives
    only initials). 'T. Harry Williams' is not 'Tennessee Williams'; a lone surname is no one in particular."""
    pa, pb = name_words(a), name_words(b)
    if not pa or not pb: return False
    fold = lambda w: w.lower().strip('.').replace('ë', 'e')
    if fold(pa[-1]) != fold(pb[-1]): return False
    if len(pa) == 1 or len(pb) == 1: return len(pa) == len(pb) == 1 and fold(pa[0]) in single_names
    ga, gb = goes_by(pa[:-1]), goes_by(pb[:-1])
    if ga and gb: return fold(ga) == fold(gb)
    return fold(pa[0])[0] == fold(pb[0])[0]


# a sale whose listing says it is signed. "Single signature" is a binding term, not an autograph.
SIGNED_SALE = re.compile(r"\bsigned\b|inscribed|autograph|(?:\bwith\s+|\bw/\s*)(?:(?:his|her|the|an?|author'?s?|original|tipped[- ]in)\s+){0,2}"
                         r"signature\b|\btipped[- ]in signature\b", re.I)


def same_name(a, b):
    """One given name, allowing a seller's spelling (Steven for Stephen, Phillip for Philip): the same first two letters
    and most of the rest. Jane and John, or Harry and Henry, differ."""
    a, b = a.lower().strip('.'), b.lower().strip('.')
    return a == b or (a[:2] == b[:2] and difflib.SequenceMatcher(None, a, b).ratio() >= 0.75)


def names_agree(mine, printed):
    """Whether the given names a listing prints with an author's surname can be the copy author's (mine, as name_words
    gives them): the names they go by agree (Jane is not John, Tennessee is not T. Harry), or the initials do where one
    side gives only initials (Christopher is not J. R. R.); and where both give a middle name, the last ones agree (John
    Paul Adams is not John Quincy Adams, nor John Q. Adams). A middle name only one of them prints is no disagreement."""
    f = lambda w: w.lower().strip('.')
    spelled = lambda ws: [w for w in ws if not INITIALS.fullmatch(w)]
    ga, gb = goes_by(mine), goes_by(printed)
    if not gb: return all(f(w)[0] in {f(x)[0] for x in mine} for w in printed)      # initials, or a middle initial alone
    if not ga: return f(gb)[0] == f(mine[0])[0]          # C. S. Lewis goes by his first initial's name, not Sinclair
    if not any(same_name(ga, w) for w in spelled(printed)) and not any(same_name(gb, w) for w in spelled(mine)): return False
    ma, mb = mine[mine.index(ga) + 1:], printed[printed.index(gb) + 1:]
    if ma and mb:
        a, b = ma[-1], mb[-1]
        return f(a)[0] == f(b)[0] if INITIALS.fullmatch(a) or INITIALS.fullmatch(b) else same_name(a, b)
    return True


def printed_given(text, surname, known, skip=()):
    """The given names a listing prints with each mention of a surname: the words just before it ("jane smith"), or just
    after it where it comes first ("smith, jane"), as long as they are initials or known given names and not words of the
    book's title (skip). One list per mention, empty where no given name is printed."""
    def part(w):
        w = w.strip('.') + ('.' if w.endswith('.') else '')
        if re.fullmatch(r'(?:[a-z]\.){2,3}', w): return re.findall(r'[a-z]\.', w)            # "j.r.r." -> j. r. r.
        if re.fullmatch(r'[a-z]\.|[b-hj-z]', w): return [w]
        b = w.rstrip('.')
        return [b] if b in known and b not in skip else None
    out = []
    for m in re.finditer(r'(?<![\w\'])' + re.escape(surname).replace('e', '[eë]') + r'(?![\w\'])', text):
        run = []
        for w in reversed(text[:m.start()].split()[-4:]):
            p = None if re.search(r'[,;:/()]$', w) else part(w)
            if p is None or len(run) >= 3: break
            run[:0] = p
        if not run:
            after = re.match(r'\s*,\s*(.*)', text[m.end():])
            for w in (after.group(1).split()[:3] if after else []):
                p = part(re.sub(r'[,;:]$', '', w))
                if p is None: break
                run += p
                if re.search(r'[,;:]$|\w\w\.$', w): break          # "smith, jane. the garden"
        out.append(run)
    return out


TOKEN = re.compile(r'[a-z0-9]+')      # the index's words: a word-bounded match of a text always holds each of these whole
# the only characters re.I takes for an ASCII letter once text is lowercased ('İ' and 'K' lowercase to plain letters)
REGEX_TWINS = str.maketrans({'ı': 'i', 'ſ': 's'})


def tokens(s):
    return TOKEN.findall((s or '').lower().translate(REGEX_TWINS))


def lookup_words(t):
    """The words of a title a title_rx match must hold whole: those without an apostrophe (the pattern lets "don't" match
    "dont"), as title_rx cleans the title."""
    t = re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', t or '')
    return ' '.join(w for w in words(t) if "'" not in w)


def name_key(n):
    """The surname name_rx(n) matches whole, cleaned as it cleans it."""
    parts = re.sub(r"[^\w\s\.'ë]", '', n or '').replace('.', ' ').split()
    return parts[-1] if parts else ''


def words(s):
    return re.findall(r"[a-z0-9']+", (s or '').lower().replace('’', "'"))


def title_rx(t, short_ok=False):
    """A regex for a spine title, or None when the title is too short or generic to match on its own.
    short_ok allows any title, for use together with the author's surname."""
    t = re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', t or '')
    w = words(t)
    sig = [x for x in w if x not in STOP]
    if not w or (not short_ok and len(sig) < 2 and not any(len(x) >= 8 for x in sig)): return None
    return re.compile(r'\b' + r"[\s\W]+".join(re.escape(x).replace("'", "[’']?") for x in w) + r'\b', re.I)


class Reference:
    def __init__(self, data_dir=DATA, price_files=None):
        self.data_dir = data_dir
        env = os.environ.get('SHELFSCAN_PRICE_FILES')
        paths = price_files or ([p.strip() for p in env.split(',') if p.strip()] if env else
                                [os.path.join(data_dir, f) for f in DEFAULT_PRICE_FILES] if data_dir != DATA else fetch(DEFAULT_PRICE_FILES))
        self.rules = json.load(open(os.path.join(data_dir, 'rules.json')))
        rows, self.price_version = load_prices(paths)
        out = {c.lower() for c in self.rules.get('sold_conditions_left_out', [])}
        self.prices = [r for r in rows if r['condition'].lower() not in out]
        if len(self.prices) < len(rows):
            self.price_version['left_out_by_condition'] = len(rows) - len(self.prices)
            self.price_version['rows'] = len(self.prices)
        self._index = collections.defaultdict(list)          # word -> the positions of the sales whose text holds it
        for i, r in enumerate(self.prices):
            for t in set(tokens(r['text'])): self._index[t].append(i)
        self._collections = None
        self._author_names = None          # built on first use, by is_author_name
        names = open(os.path.join(data_dir, 'given-names.txt')).read()
        self.given_names = set(re.sub(r'#.*', ' ', names).split())
        self.single_names = {w.lower().replace('ë', 'e') for w in self.rules.get('single_name_authors', [])} | \
                            {w.lower() for w in self.rules.get('single_name_authors', [])}
        lines = open(os.path.join(data_dir, 'frequent-authors.tsv')).read().splitlines()[1:]
        self.frequent = []
        for ln in lines:
            f = ln.split('\t')
            if len(f) >= 3 and name_rx(f[1], self.single_names): self.frequent.append((f[1], name_rx(f[1], self.single_names), money(f[2])))
        self.watch = [(n, name_rx(n, self.single_names)) for n in open(os.path.join(data_dir, 'watchlist.txt')).read().splitlines()
                      if n.strip() and name_rx(n, self.single_names)]
        for n in {r['author'] for r in self.prices if r['author']} | {n for n, *_ in self.frequent} | {n for n, _ in self.watch}:
            self.given_names |= {w.lower().strip('.') for w in name_words(n)[:-1] if not INITIALS.fullmatch(w) and w[:1].isalpha()}
        R = self.rules
        self.rx = {k: re.compile(R[k], re.I) for k in ('book_club_sure', 'book_club_possible', 'reprint_publishers', 'collectible_imprints',
                                                       'collectible_marks', 'false_friend_authors', 'illustrators')}
        self.always_pb = re.compile('|'.join(R['always_check_paperback_authors']), re.I)
        self.whitelist = [dict(e, _a=re.compile(e['author'], re.I) if e['author'] else None, _t=re.compile(e['title'], re.I) if e['title'] else None,
                               _p=re.compile(e['publisher'], re.I) if e['publisher'] else None) for e in R['paperback_whitelist']]

    @property
    def collections(self):
        """The story-collection catalog and whitelist (shelfscan.anthologies), over the store the app uses."""
        if self._collections is None:
            from .anthologies import Collections
            from .store import open_store
            home = os.environ.get('SHELFSCAN_HOME')
            try: store = open_store(os.path.join(home, 'store') if home else None)
            except Exception: store = None
            self._collections = Collections(store, self.rules.get('collection_whitelist'))
        return self._collections

    def version(self):
        return {'prices': self.price_version, 'rules': self.rules['version']}

    def having(self, *texts):
        """The sales whose text holds, for any one of these texts, that text's rarest word, in file order: every sale a
        word-bounded match of the text could find, and few more. A text with no word to look up gives every sale."""
        ids = set()
        for t in texts:
            runs = tokens(t)
            if not runs: return self.prices
            ids.update(min((self._index.get(w, ()) for w in runs), key=len))
        return [self.prices[i] for i in sorted(ids)]

    def is_author_name(self, title):
        """Whether a title is the full name of an author the sales or the lists know ('Sinclair Lewis', 'Geoffrey Chaucer'):
        such a book is about them."""
        ws = name_words(re.split(r'\s*[:;]', title or '')[0])
        if not 2 <= len(ws) <= 4: return False
        if self._author_names is None:
            names = {r['author'] for r in self.prices if r['author']} | {n for n, *_ in self.frequent} | {n for n, _ in self.watch}
            self._author_names = {' '.join(w.lower().strip('.') for w in name_words(n)) for n in names if len(name_words(n)) >= 2}
        return ' '.join(w.lower().strip('.') for w in ws) in self._author_names

    def names_fit(self, author, text, title=''):
        """False when a listing prints the author's surname only with someone else's given names: Jane Smith's sale for John
        Smith's book, Tennessee Williams's for T. Harry Williams's, John Paul Adams's for John Quincy Adams's. A mention with
        no given name printed, or a surname alone on the spine, is no disagreement."""
        skip, agree, disagree = set(tokens(title)), False, False
        for n in re.split(r'\s+(?:and|&|with)\s+|;', re.sub(r'\(.*?\)', ' ', author or '')):
            ws = name_words(n)
            if len(ws) < 2: continue
            for printed in printed_given(text, ws[-1].lower().strip('.').replace('ë', 'e'), self.given_names, skip):
                if not printed: continue
                if names_agree(ws[:-1], printed): agree = True
                else: disagree = True
        return agree or not disagree

    def find_frequent(self, author):
        if not author: return None
        return next(((n, med) for n, rx, med in self.frequent if rx.search(author) and same_person(n, author, self.single_names)), None)

    def find_watch(self, author):
        if not author: return None
        return next((n for n, rx in self.watch if rx.search(author) and same_person(n, author, self.single_names)), None)

    def comps(self, author, title, old=False, about=False):
        """Sold listings for this title, and whether they were matched together with the author.
        With an author read, any title counts but the listing must name one of the authors' surnames; without one, only a
        distinctive title of three or more significant words counts. An old copy that finds nothing either way may also match
        on two distinctive words ('Arabian Nights', 'The Secret Garden'), whoever is named: editors and translators get read
        as authors, and old spines often name no author. Those matches use only listings not dated 1970 or later.
        Matches on the title alone must point to one book (see one_book), so "Who's Who" or "World War II" find nothing.
        about: the book is about a person and by someone else (Mark Schorer's Sinclair Lewis), so only listings naming its
        own author count, never the subject's books."""
        surs = surnames(author)
        if surs:
            srx = re.compile(r'\b(' + '|'.join(map(re.escape, surs)) + r')\b')
            by_author = [r for r in self.having(*surs) if any(s in r['text'] for s in surs) and srx.search(r['text'])
                         and self.names_fit(author, r['text'], title)]
            main = re.split(r'\s*[:;(]|\s+-\s+', title or '')[0]          # 'Bambi: A Life in the Woods' -> 'Bambi'
            for t in dict.fromkeys([title, main]):
                trx = title_rx(t, short_ok=True)
                hits = [r for r in by_author if trx.search(r['text'])] if trx else []
                if hits: return hits, True
        else:
            trx = title_rx(title)
            if trx and len([w for w in words(title) if w not in STOP]) >= 3 and not about:
                hits = [r for r in self.having(lookup_words(title)) if trx.search(r['text'])]
                if hits and one_book(hits, title): return hits, False
        if old and not about:
            whole = ' '.join(words(re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', title or '')))
            for t in title_cores(title):
                trx = title_rx(t)
                hits = [r for r in self.having(lookup_words(t)) if trx and trx.search(r['text']) and not (r['year'] and r['year'] >= 1970)]
                if hits and one_book(hits, t) and (len(hits) > 1 or t == whole): return hits, False      # one sale won't value a trimmed title
        return [], bool(surs)

    def signed_comps(self, author):
        rx = name_rx(author, self.single_names) if author else None
        if not rx: return []
        return [r for r in self.having(name_key(author)) if rx.search(r['text']) and self.names_fit(author, r['text']) and (r['signed'] or SIGNED_SALE.search(r['text']))]


    def author_comps(self, author):
        """Unsigned single-copy sales of anything by this author: a rough guide to a book whose own title has no sales."""
        rx = name_rx(author, self.single_names) if author else None
        if not rx: return []
        return [r for r in self.having(name_key(author)) if rx.search(r['text']) and self.names_fit(author, r['text']) and not r['signed']
                and not SIGNED_SALE.search(r['text']) and not re.search(r'\blot\b|\bset of\b|facsimile|reprint', r['text'])]


def surname(author):
    w = [x for x in words(re.sub(r'\(.*?\)', ' ', author or '')) if len(x) > 2 and x not in STOP and x not in ('jr', 'sir')]
    return w[-1] if w else None


def surnames(author):
    """'Kate Douglas Wiggin and Nora A. Smith' -> ['wiggin', 'smith']"""
    names = re.split(r'\s+(?:and|&|with)\s+|;', re.sub(r'\(.*?\)', ' ', author or ''))
    return list(dict.fromkeys(s for s in map(surname, names) if s))


def title_cores(title):
    """The title, its main title, and each with generic words trimmed from the end ("Arabian Nights' Entertainments" ->
    "arabian nights'"), keeping only those with two or more distinctive words. Only the end is trimmed: 'The Story of Peter Pan'
    is a different book from the Peter Pan titles that sell. Trimming stops at a little word like "in" or "of": the phrase it
    starts is part of what the book is ("Who's Who in Children's Books" isn't "Who's Who in Indian Relics")."""
    def plain(x): return x.strip("'") not in STOP and x.strip("'") not in GENERIC and len(x) >= 3 and not x.isdigit()
    t = re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', title or '')
    out = []
    for s in (t, re.split(r'\s*[:;]|\s+-\s+', t)[0]):
        w = words(s)
        core = w[:]
        while core and not plain(core[-1]) and core[-1].strip("'") not in STOP: core = core[:-1]
        if core and core[-1].strip("'") in STOP and len(core) < len(w): core = w          # trimmed up to a phrase: keep it whole
        for c in (w, core):
            if sum(map(plain, c)) >= 2: out.append(' '.join(c))
    return list(dict.fromkeys(out))


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None
