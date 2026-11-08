"""The author of a book whose spine shows none, from free book catalogs: Open Library, then the Internet Archive.

Asked only about the books rules.wants_author picks: a whole title read, no author, and no sales found for the title on
its own. Open Library's general search gets the title and the publisher the spine shows; the Internet Archive gets the
title as a phrase. A record counts when its title is the spine's (give or take a leading article, with or without its
subtitle), it names a person as author (not a publisher, museum or "staff"), its publisher, when the spine shows one
clearly and the record names any, is the spine's, and its year fits the spine's look (an antique by 1930, an old book by
1975). The answer is the author of at least two thirds of the records that count.

Chosen 8 November 2026 over the Library of Congress (as good, but about 7 seconds a title), Wikidata (almost no books)
and a web search (better on small presses, but about a cent a book). On a hand-checked sample of 145 authorless books
from the shop's photos, Open Library then the Internet Archive named the right author for 47 and a wrong one for 4."""
import collections, json, re, threading, time, urllib.parse, urllib.request
from .rules import PARTIAL, bare_title, clean, wants_author
from .refdata import name_words, words

UA = 'TomeRaider/1.0 (+https://github.com/mbuchoff/tome-raider)'
TIMEOUT = 15
PAUSE = 0.4                 # seconds between requests to one catalog, across all the photos being run
NOISE = {'press', 'books', 'publishing', 'publishers', 'company', 'house', 'library', 'inc', 'the', 'and'}
CORP = re.compile(r'\b(?:library|congress|copyright|collection|inc|incorporated|ltd|books|publishing|publishers|press|society|'
                  r'editions|company|group|museum|staff|editors|corporation|bible|world book|time-life|musical|author|'
                  r'university|institute|association|department|dept|council|magazine|firm)\b', re.I)
ROLE = re.compile(r',?\s*\b(?:\d{3,4}\??-(?:\d{3,4})?|author|writer|editor|compiler|illustrator|translator|joint author|ed|comp)\b\.?', re.I)
_last, _lock = collections.defaultdict(float), threading.Lock()


def _get(url, params):
    """The JSON a catalog answers with, at most one request every PAUSE seconds per catalog."""
    host = url.split('/')[2]
    with _lock:
        wait = _last[host] + PAUSE - time.time()
        if wait > 0: time.sleep(wait)
        _last[host] = time.time()
    req = urllib.request.Request(url + '?' + urllib.parse.urlencode(params, doseq=True), headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r: return json.load(r)


def plain(t):
    """A title's words, without the reader's question marks, notes in brackets or a volume number."""
    t = re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', t or '')
    return re.sub(r'\bvols?\s+[ivx\d].*$', '', ' '.join(words(t))).strip()


def bare(t):
    t = plain(t)
    for a in ('the ', 'a ', 'an '):
        if t.startswith(a): return t[len(a):]
    return t


def publisher_words(imprint):
    if not imprint or '?' in imprint: return []
    return [w for w in re.findall(r'[a-z]+', imprint.lower()) if len(w) > 2 and w not in NOISE][:2]


def person(n):
    return len(re.findall(r"[A-Za-z][A-Za-z'.\-]+", n or '')) >= 2 and not CORP.search(n)


def surname(n):
    w = [x for x in re.findall(r"[a-z']+", (n or '').lower()) if x not in ('jr', 'sr', 'ii', 'iii')]
    return w[-1] if w else ''


def uninvert(c):
    """'Fenn, Ada, 1902-' -> 'Ada Fenn'."""
    c = ROLE.sub('', c or '').strip(' ,.')
    if ',' in c:
        last, first = c.split(',', 1)
        c = f'{first.strip()} {last.strip()}'
    return re.sub(r'\s+', ' ', c).strip(' ,.')


def fits_look(year, look):
    y = re.search(r'1[5-9]\d\d|20[0-2]\d', str(year or ''))
    return not y or {'antique': int(y.group(0)) <= 1930, 'old': int(y.group(0)) <= 1975}.get(look, True)


def listify(v): return v if isinstance(v, list) else [v] if v else []


def open_library(b):
    q = ' '.join([plain(b['title'])] + publisher_words(b.get('imprint')))
    docs = _get('https://openlibrary.org/search.json', {'q': q, 'fields': 'title,subtitle,author_name,publisher,first_publish_year',
                                                        'limit': 20}).get('docs', [])
    return [dict(title=d.get('title') or '', sub=d.get('subtitle') or '', author=(d.get('author_name') or [''])[0],
                 publishers=d.get('publisher') or [], year=d.get('first_publish_year')) for d in docs]


def internet_archive(b):
    docs = _get('https://archive.org/advancedsearch.php', {'q': f'title:("{plain(b["title"])}")', 'fl[]': ['title', 'creator', 'publisher', 'date'],
                                                          'rows': 25, 'output': 'json'})['response']['docs']
    out = []
    for d in docs:
        main, _, sub = (listify(d.get('title')) or [''])[0].partition(' : ')
        out.append(dict(title=main, sub=sub, author=uninvert((listify(d.get('creator')) or [''])[0]), publishers=listify(d.get('publisher')),
                        year=(d.get('date') or '')[:4]))
    return out


CATALOGS = [('Open Library', open_library), ('Internet Archive', internet_archive)]


def author_from(records, b):
    """The author most of the records that are this book agree on, with one of those records; or None."""
    want, pw = bare(b['title']), publisher_words(b.get('imprint'))
    same = [r for r in records if person(r['author']) and want in (bare(r['title']), bare(r['title'] + ' ' + r['sub']))
            and fits_look(r['year'], (b.get('look') or '').lower())
            and not (pw and r['publishers'] and not any(w in ' '.join(r['publishers']).lower() for w in pw))]
    if not same: return None
    top, k = collections.Counter(surname(r['author']) for r in same).most_common(1)[0]
    if k * 3 < len(same) * 2: return None
    return next(r for r in same if surname(r['author']) == top)


def find(b):
    """dict(author, source, title, publisher, year) from the first catalog that names the book's author;
    dict(author='', tried=[...]) when none does; None when a catalog failed and none named one, so a later run asks again."""
    failed = False
    for name, ask in CATALOGS:
        try: r = author_from(ask(b), b)
        except Exception as e:                  # a catalog that's down or slow costs the book its lookup, nothing more
            print(f'{name}: no answer for {clean(b.get("title"))!r} ({type(e).__name__}: {e})', flush=True)
            failed = True
            continue
        if r: return dict(author=r['author'], source=name, title=(r['title'] + (': ' + r['sub'] if r['sub'] else ''))[:120],
                          publisher=', '.join(r['publishers'][:1])[:80], year=r['year'])
    return None if failed else dict(author='', tried=[n for n, _ in CATALOGS])


def add_authors(books, ref):
    """Looks up every book that wants an author (rules.wants_author) and keeps the answer on its reading as 'lookup', so a
    replay never asks again. A book with no author whose title another book on the photo has, with an author, is taken to
    be by that author first: a copy of "Harbor Lights" with no author next to one by Ada Fenn is hers, not the one other
    "Harbor Lights" in the price files. Returns how many authors were found."""
    n = 0
    by_title = {}
    for b in books:
        if name_words(clean(b.get('author'))) and bare_title(b.get('title')): by_title.setdefault(bare_title(b['title']), clean(b['author']))
    for b in books:
        if 'lookup' in b or name_words(clean(b.get('author'))) or PARTIAL.search(b.get('title') or ''): continue
        other = by_title.get(bare_title(b.get('title')))
        if other:
            b['lookup'] = dict(author=other, source='another copy on this photo')
            n += 1
            continue
        if not wants_author(b, ref): continue
        got = find(b)
        if got is None: continue
        b['lookup'] = got
        n += bool(got['author'])
    return n
