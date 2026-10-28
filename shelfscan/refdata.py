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
import collections, csv, datetime, hashlib, json, os, re, statistics
from .xlsx_read import read as read_xlsx

DATA = os.environ.get('SHELFSCAN_DATA') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
DEFAULT_PRICE_FILES = ['sales_report.xlsx', 'master_list_2022.xlsx', 'first_editions.xlsx', 'easton_franklin.xlsx']


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
        with open(path, newline='', encoding='utf-8-sig') as f: yield os.path.basename(path), list(csv.reader(f))
    else:
        for name, rows in read_xlsx(path).items(): yield name, rows


def load_prices(paths):
    """Rows of dict(title, author, price, date, year, url, image, condition, signed, publisher, text); repeated eBay item ids
    are kept once, and so is a sale an earlier file already holds (same title, price and date)."""
    rows, seen, files, earlier = [], set(), [], set()
    for path in paths:
        files.append({'file': os.path.basename(path), 'sha256': hashlib.sha256(open(path, 'rb').read()).hexdigest()[:12]})
        n0, dupes, mine = len(rows), 0, set()
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
                if m:
                    if m.group(1) in seen: continue
                    seen.add(m.group(1))
                date = to_date(get('date'))
                key = (title.lower(), round(price, 2), date)
                if key in earlier:
                    dupes += 1; continue
                mine.add(key)
                author = (get('author') or '').strip()
                ys = [int(y) for y in YEAR.findall(title)]
                image = str(get('image') or '').strip()
                rows.append(dict(title=title, author=author, price=price, date=date, url=url if url.startswith('http') else '',
                                 image=image if image.startswith('http') else '', condition=str(get('condition') or '').strip(),
                                 signed=str(get('signed') or '').strip().lower() in ('yes', 'y', 'true', '1'),
                                 publisher=str(get('publisher') or '').strip(), year=min(ys) if ys else None, text=(title + ' ' + author).lower()))
        earlier |= mine
        files[-1]['rows'] = len(rows) - n0
        if dupes: files[-1]['already_loaded'] = dupes
    dates = sorted(r['date'] for r in rows if r['date'])
    version = {'files': files, 'rows': len(rows), 'first_sale': dates[0] if dates else None, 'last_sale': dates[-1] if dates else None}
    return rows, version


def name_rx(n):
    """Matches 'First Last', 'F. Last', 'Last, First'; a single word matches as a surname."""
    parts = re.sub(r"[^\w\s\.'ë]", '', n).replace('.', ' ').split()
    if not parts: return None
    last, first = parts[-1], parts[0]
    if len(parts) == 1: return re.compile(r'\b' + re.escape(last) + r'\b', re.I)
    return re.compile(r'\b' + re.escape(first[0]) + r'[\w\.]*\.?\s+(?:[\w\.]+\s+){0,2}' + re.escape(last) + r'\b|\b'
                      + re.escape(last) + r',\s*' + re.escape(first[0]), re.I)


TOKEN = re.compile(r'[a-z0-9]+')      # the index's words: a word-bounded match of a text always holds each of these whole


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
            for t in set(TOKEN.findall(r['text'])): self._index[t].append(i)
        self._collections = None
        lines = open(os.path.join(data_dir, 'frequent-authors.tsv')).read().splitlines()[1:]
        self.frequent = []
        for ln in lines:
            f = ln.split('\t')
            if len(f) >= 3 and name_rx(f[1]): self.frequent.append((f[1], name_rx(f[1]), money(f[2])))
        self.watch = [(n, name_rx(n)) for n in open(os.path.join(data_dir, 'watchlist.txt')).read().splitlines() if n.strip()]
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
            runs = TOKEN.findall((t or '').lower())
            if not runs: return self.prices
            ids.update(min((self._index.get(w, ()) for w in runs), key=len))
        return [self.prices[i] for i in sorted(ids)]

    def find_frequent(self, author):
        if not author: return None
        return next(((n, med) for n, rx, med in self.frequent if rx.search(author) and same_first_name(n, author)), None)

    def find_watch(self, author):
        if not author: return None
        return next((n for n, rx in self.watch if rx.search(author) and same_first_name(n, author)), None)

    def comps(self, author, title, old=False):
        """Sold listings for this title, and whether they were matched together with the author.
        With an author read, any title counts but the listing must name one of the authors' surnames; without one, only a
        distinctive title of three or more significant words counts. An old copy that finds nothing either way may also match
        on two distinctive words ('Arabian Nights', 'The Secret Garden'), whoever is named: editors and translators get read
        as authors, and old spines often name no author. Those matches use only listings not dated 1970 or later."""
        surs = surnames(author)
        if surs:
            srx = re.compile(r'\b(' + '|'.join(map(re.escape, surs)) + r')\b')
            by_author = [r for r in self.having(*surs) if any(s in r['text'] for s in surs) and srx.search(r['text'])]
            main = re.split(r'\s*[:;(]|\s+-\s+', title or '')[0]          # 'Bambi: A Life in the Woods' -> 'Bambi'
            for t in dict.fromkeys([title, main]):
                trx = title_rx(t, short_ok=True)
                hits = [r for r in by_author if trx.search(r['text'])] if trx else []
                if hits: return hits, True
        else:
            trx = title_rx(title)
            if trx and len([w for w in words(title) if w not in STOP]) >= 3:
                hits = [r for r in self.having(lookup_words(title)) if trx.search(r['text'])]
                if hits: return hits, False
        if old:
            for t in title_cores(title):
                trx = title_rx(t)
                hits = [r for r in self.having(lookup_words(t)) if trx and trx.search(r['text']) and not (r['year'] and r['year'] >= 1970)]
                if hits: return hits, False
        return [], bool(surs)

    def signed_comps(self, author):
        rx = name_rx(author) if author else None
        if not rx: return []
        return [r for r in self.having(name_key(author)) if rx.search(r['text']) and (r['signed'] or re.search(r'\bsigned\b|inscribed|autograph', r['text']))]


    def author_comps(self, author):
        """Unsigned single-copy sales of anything by this author: a rough guide to a book whose own title has no sales."""
        rx = name_rx(author) if author else None
        if not rx: return []
        return [r for r in self.having(name_key(author)) if rx.search(r['text']) and not r['signed']
                and not re.search(r'\bsigned\b|inscribed|autograph|\blot\b|\bset of\b|facsimile|reprint', r['text'])]


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
    is a different book from the Peter Pan titles that sell."""
    def plain(x): return x.strip("'") not in STOP and x.strip("'") not in GENERIC and len(x) >= 3 and not x.isdigit()
    t = re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', title or '')
    out = []
    for s in (t, re.split(r'\s*[:;]|\s+-\s+', t)[0]):
        w = words(s)
        core = w[:]
        while core and not plain(core[-1]): core = core[:-1]
        for c in (w, core):
            if sum(map(plain, c)) >= 2: out.append(' '.join(c))
    return list(dict.fromkeys(out))


def same_first_name(ref_name, author):
    """False when both names give a full first name and they differ ('Clive Barker' vs 'Cicely M. Barker')."""
    a, b = re.findall(r"[a-z']+", ref_name.lower()), re.findall(r"[a-z']+", (author or '').lower())
    return not (len(a) >= 2 and len(b) >= 2 and len(a[0]) > 2 and len(b[0]) > 2 and a[0] != b[0])


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None
