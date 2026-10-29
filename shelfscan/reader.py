"""Finding shelves and reading spines with Claude, then removing books listed by two overlapping crops."""
import concurrent.futures as cf, difflib, json, re
from . import images
from .claude import SONNET, OPUS

SHELF_PROMPT = """This is a photo of bookshelves. Find each shelf row of books, top to bottom.
For each row give the band that contains the books standing on it: from slightly above the top of the tallest book to the shelf surface they stand on.
Include a row cut off at the photo edge if its spines are visible. Give positions as percentages of the photo height.
Reply with JSON only: {"shelves": [{"shelf": 1, "top_pct": 4.5, "bottom_pct": 33.0}, ...]}"""

COLS = ['pos', 'x0_pct', 'x1_pct', 'y0_pct', 'y1_pct', 'spine_text', 'author', 'title', 'imprint', 'format', 'jacket', 'look', 'appearance', 'marks', 'legibility', 'confidence']

FIELDS = """Reply with TSV only: a header row, then one row per book, no prose and no code fence. Columns:
""" + '\t'.join(COLS) + """
- pos: 1, 2, 3... from the left. x0_pct, x1_pct: the book's left and right edges as percentages of the image width; read them off the
  ruler along the top. y0_pct, y1_pct: its top and bottom edges as percentages of the image height (0 is the top of the ruler band).
- spine_text: the words on the spine exactly as printed, " / " between separate lines; "" if none are legible.
- author, title, imprint: as best you can read them. Put "?" after a guessed word, "..." where text is cut off, "(unreadable)" if none of it is legible. Don't fill in words you can't see.
- format: hardcover, paperback or unclear. Say unclear unless the binding is plain to see. jacket: yes, no or unclear.
- look: modern, old (about 1925-1970) or antique (pre-1925).
- appearance: up to 6 words that would help someone spot it on the shelf: spine colour, lettering, size.
- marks: anything that bears on value, briefly: book club, series name or number, ex-library label, "first edition" or bestseller banners, signed, limited, slipcase, leather, damage, flat.
- legibility: clear, partial or unreadable. confidence: 0 to 1, that author and title are right."""

READ_PROMPT = """This is part of one shelf in a bookshelf photo, for a tool that spots collectible books. {lines}
List every book whose spine center lies inside your region, left to right, including spines you can't read; neighbouring crops handle the rest.
Include books lying flat, and mark them "flat" in marks.{note}

""" + FIELDS

# added to the reading prompt on extremely rare and signed shelves; other shelves keep the prompt (and saved responses) unchanged
STICKER_NOTE = (' This shelf holds signed and high-value books: if a spine or jacket carries a sticker or label saying signed,'
                ' autographed, signed by the author or similar, write "signed sticker" in marks.')

FLAT_PROMPT = """This is part of one shelf in a bookshelf photo, for a tool that spots collectible books. {lines}
Another pass has already listed the upright spines. Look only for books in your region that are easy to miss: books lying flat on top of
or in front of other books, horizontal stacks, and spines partly hidden behind or between other books. List each one; mark it "flat" or
"hidden" in marks. If there are none, reply with the header row only.

""" + FIELDS


REREAD_PROMPT = """These are close-ups of book spines from a bookshelf photo, numbered above each one, for a tool that spots collectible books.
An earlier pass at lower magnification couldn't read them well. Read each one as carefully as you can.

Reply with TSV only: a header row, then one row per number, no prose and no code fence. Columns:
n\tspine_text\tauthor\ttitle\timprint\tformat\tlook\tmarks\tlegibility\tconfidence
- spine_text: the words on the spine exactly as printed, " / " between separate lines; "" if none are legible.
- author, title, imprint: as best you can read them. Put "?" after a guessed word, "(unreadable)" if none of it is legible. Don't fill in words you can't see.
- format: hardcover, paperback or unclear. look: modern, old (about 1925-1970) or antique (pre-1925).
- marks: anything that bears on value, briefly: book club, series, ex-library, signed, limited, leather, translator or illustrator credits.
- legibility: clear, partial or unreadable. confidence: 0 to 1, that author and title are right."""
REREAD_COLS = ['n', 'spine_text', 'author', 'title', 'imprint', 'format', 'look', 'marks', 'legibility', 'confidence']
REREAD_MAX = 24             # spines per photo; two sheets at most

FOOT_PROMPT = """These are close-ups of the feet (bottom ends) of book spines from a bookshelf photo, numbered above each one, for a tool
that spots collectible books. Publishers, reprint series and book clubs put their names or logos here.

Reply with TSV only: a header row, then one row per number, no prose and no code fence. Columns:
n\tfoot_text\tlogo\timprint\tbook_club\treprint
- foot_text: any words at the foot exactly as printed, " / " between lines; "" if none are legible.
- logo: a few words describing any logo or device; "" if none.
- imprint: the publisher, if the words or logo identify it; "" if not.
- book_club: yes if a book club's name or logo is there, for example Book Club Associates' small lowercase "bca" (or "bc")
  monogram in a box, SFBC, Readers Union, Book-of-the-Month Club, Nelson Doubleday or Literary Guild; no if a trade publisher's
  name or logo is there; unclear otherwise.
- reprint: the reprint publisher or series if one is named or its logo shown (such as Calla, Dover, Everyman's Library); else ""."""
FOOT_COLS = ['n', 'foot_text', 'logo', 'imprint', 'book_club', 'reprint']
FOOT_MAX = 36               # spine feet per photo; two sheets of three rows

# when a candidate copy was printed, from its whole spine: the bookshop matches sales of the same years ("versions will come
# out many years apart. Use the color and details seen on the spine to find the year of that edition")
DATE_PROMPT = """These are whole book spines from a bookshelf photo, numbered above each one, for a tool that values used books
from sold listings. Editions of a book can come out many years apart and sell for very different prices, so the tool needs
the years this copy was printed, not when the work first appeared. What was read from each spine:
{books}

For each number, estimate the years this copy was printed from what its spine shows: the colours, the cloth, paper or leather,
the lettering and typefaces, the decoration, the publisher's name and logo and how they looked at the time, a series design,
a price, an ISBN or barcode (1970 or later), and the wear. Use what you know of how this publisher's editions of this book
looked over the years. Give the narrowest range you can defend: a wide range (1900-1960) is better than a wrong narrow one.

Reply with TSV only: a header row, then one row per number, no prose and no code fence. Columns:
n\tfrom\tto\tclues
- from, to: the earliest and latest year this copy could have been printed; both "" if you can't tell.
- clues: the details that placed it, under 15 words."""
DATE_MAX = 40               # candidate spines per photo


def parse(text):
    rows = []
    for ln in text.replace('```tsv', '').replace('```', '').strip().splitlines():
        f = ln.split('\t')
        if len(f) < 6 or f[0].strip().lower() == 'pos': continue
        rows.append({k: v.strip() for k, v in zip(COLS, (f + [''] * len(COLS))[:len(COLS)])})
    return rows


def num(s, default=None):
    try: return float(str(s).strip().rstrip('%'))
    except ValueError: return default


def norm(s):
    s = (s or '').lower().replace('’', "'")
    if 'unreadable' in s: return ''
    s = re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', s)
    return re.sub(r'\s+', ' ', re.sub(r"[^a-z0-9' ]", ' ', s)).strip()


def similar(a, b):
    a, b = norm(a), norm(b)
    return bool(a and b) and difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def find_shelves(claude, im, budget):
    text = claude.ask(SONNET, images.overview(im), SHELF_PROMPT, 1000, 1000, budget)
    m = re.search(r'\{.*\}', text, re.S)
    bands = json.loads(m.group(0))['shelves'] if m else []
    bands = sorted((b for b in bands if num(b.get('top_pct')) is not None and num(b.get('bottom_pct')) is not None), key=lambda b: num(b['top_pct']))
    for i, b in enumerate(bands, 1):
        b['shelf'], b['top_pct'], b['bottom_pct'] = i, num(b['top_pct']), num(b['bottom_pct'])
    return bands


def _rows(tile, text, source):
    out = []
    for r in parse(text):
        a, b = num(r['x0_pct']), num(r['x1_pct'])
        if a is None or b is None: continue
        a, b = sorted((max(0, min(100, a)), max(0, min(100, b))))
        x0, x1 = tile['x0'] + a / 100 * tile['w'], tile['x0'] + b / 100 * tile['w']
        # vertical extent, measured on the crop including its ruler band; fall back to the whole shelf band
        H = tile['h'] + images.RULER
        c, d = num(r['y0_pct']), num(r['y1_pct'])
        y0, y1 = tile['y0'], tile['y0'] + tile['h']
        if c is not None and d is not None and d - c >= 3:
            c, d = sorted((max(0, min(100, c)), max(0, min(100, d))))
            y0 = max(tile['y0'], round(tile['y0'] + c / 100 * H - images.RULER))
            y1 = min(tile['y0'] + tile['h'], max(y0 + 20, round(tile['y0'] + d / 100 * H - images.RULER)))
        r.update(shelf=tile['shelf'], tile=tile['idx'], source=source, x0=round(x0), x1=round(max(x1, x0 + 8)), xc=(x0 + x1) / 2,
                 y0=y0, y1=y1, own=tile['own'], confidence=num(r['confidence'], 0.5))
        out.append(r)
    return out


def overlap(a, b):
    """The share of the narrower and of the shorter box that the two boxes have in common."""
    ox, oy = min(a['x1'], b['x1']) - max(a['x0'], b['x0']), min(a['y1'], b['y1']) - max(a['y0'], b['y0'])
    if ox <= 0 or oy <= 0: return 0, 0
    return ox / max(1, min(a['x1'] - a['x0'], b['x1'] - b['x0'])), oy / max(1, min(a['y1'] - a['y0'], b['y1'] - b['y0']))


def stacked(k, r):
    """The same book listed twice: boxes mostly on top of each other, and similar titles."""
    fx, fy = overlap(k, r)
    return fx >= 0.6 and fy >= 0.5 and similar(k['title'], r['title'])


def drop_stacked(books):
    """After the close-ups have renamed some spines, drops the less confident of any two that turn out to be one book."""
    drop = set()
    for i, a in enumerate(books):
        for j in range(i + 1, len(books)):
            b = books[j]
            if i in drop or j in drop or a['shelf'] != b['shelf'] or not stacked(a, b): continue
            drop.add(j if (a['confidence'], a['legibility'] == 'clear') >= (b['confidence'], b['legibility'] == 'clear') else i)
    return [b for i, b in enumerate(books) if i not in drop]


def dedupe(rows, tol=60, near=90):
    """Drop rows far outside their crop's own region, then books listed twice: by two neighbouring crops (same place,
    similar title), or with boxes mostly on top of each other, as happens with flat books and at the photo's edge."""
    rows = [r for r in rows if r['own'][0] - tol <= r['xc'] <= r['own'][1] + tol]
    def outside(r): return max(0, r['own'][0] - r['xc'], r['xc'] - r['own'][1])
    keep = []
    for r in sorted(rows, key=lambda r: (r['shelf'], r['xc'])):
        dup = next((k for k in keep[-6:] if k['shelf'] == r['shelf'] and k['tile'] != r['tile'] and abs(k['xc'] - r['xc']) <= near
                    and (similar(k['title'], r['title']) or (not norm(k['title']) and not norm(r['title'])))), None)
        dup = dup or next((k for k in keep if k['shelf'] == r['shelf'] and stacked(k, r)), None)
        if dup is None: keep.append(r)
        elif (outside(r), -r['confidence']) < (outside(dup), -dup['confidence']): keep[keep.index(dup)] = r
    return keep


def reread(claude, im, books, budget):
    """Reads the least legible spines again from enlarged close-ups, and keeps whichever reading is more confident."""
    weak = [b for b in books if b.get('legibility') != 'clear' and num(b.get('confidence'), 0) < 0.6 and b['x1'] - b['x0'] < 250
            and 'flat' not in (b.get('marks') or '').lower()]
    weak = sorted(weak, key=lambda b: num(b.get('confidence'), 0))[:REREAD_MAX]
    improved = 0
    for sheet, idx in images.spine_sheets(im, weak):
        text = claude.ask(SONNET, sheet, REREAD_PROMPT, 3000, 3000, budget)
        for ln in text.replace('```tsv', '').replace('```', '').strip().splitlines():
            f = ln.split('\t')
            if len(f) < 5 or f[0].strip().lower() == 'n': continue
            r = {k: v.strip() for k, v in zip(REREAD_COLS, (f + [''] * len(REREAD_COLS))[:len(REREAD_COLS)])}
            try: b = weak[idx[int(r['n']) - 1]]
            except (ValueError, IndexError): continue
            if num(r['confidence'], 0) > num(b.get('confidence'), 0) and norm(r['title']):
                for k in ('spine_text', 'author', 'title', 'imprint', 'format', 'look', 'marks', 'legibility'):
                    if r.get(k): b[k] = r[k]
                b['confidence'], b['source'] = num(r['confidence'], 0.5), b['source'] + ', close-up'
                improved += 1
    return len(weak), improved


def check_feet(claude, im, books, budget):
    """Reads the foot of each given spine from an enlarged close-up, where publishers, reprint series and book clubs put their
    names and logos, and saves it on the reading as book['foot']. Books lying flat are left out. Returns how many were read."""
    todo = [b for b in books if 'foot' not in b and 'flat' not in (b.get('marks') or '').lower()
            and b['y1'] - b['y0'] > 1.5 * (b['x1'] - b['x0'])][:FOOT_MAX]
    feet = [dict(x0=b['x0'], x1=b['x1'], y0=round(b['y1'] - 0.25 * (b['y1'] - b['y0'])),
                 y1=min(im.size[1], round(b['y1'] + 0.04 * (b['y1'] - b['y0'])))) for b in todo]
    read = 0
    for sheet, idx in images.spine_sheets(im, feet, height=260, max_sheets=2, rows=3):
        text = claude.ask(SONNET, sheet, FOOT_PROMPT, 3000, 3000, budget)
        for ln in text.replace('```tsv', '').replace('```', '').strip().splitlines():
            f = ln.split('\t')
            if len(f) < 4 or f[0].strip().lower() == 'n': continue
            r = {k: v.strip().strip('"').strip() for k, v in zip(FOOT_COLS, (f + [''] * len(FOOT_COLS))[:len(FOOT_COLS)])}
            try: b = todo[idx[int(r['n']) - 1]]
            except (ValueError, IndexError): continue
            b['foot'] = {k: r[k] for k in FOOT_COLS[1:]}
            read += 1
    return read


def date_copies(claude, im, books, budget):
    """Estimates from each given spine, whole, the years its copy was printed, and saves it on the reading as
    book['printed'] = {from, to, clues} ({} when it can't tell). Books lying flat are left out. Returns how many were dated."""
    todo = [b for b in books if 'printed' not in b and 'flat' not in (b.get('marks') or '').lower()][:DATE_MAX]
    def shown(s): return '' if not (s or '').strip() or 'unreadable' in s.lower() else s.strip()
    dated = 0
    for sheet, idx in images.spine_sheets(im, todo, height=600, max_sheets=3, rows=2):
        lines = []
        for n, i in enumerate(idx, 1):
            b = todo[i]
            bits = [f"{shown(b.get('title')) or 'title unread'}", shown(b.get('author'))]
            imprint = shown(b.get('imprint')) or shown((b.get('foot') or {}).get('imprint'))
            if imprint: bits.append(f'publisher on spine: {imprint}')
            lines.append(f'{n}. ' + '; '.join(x for x in bits if x))
        text = claude.ask(SONNET, sheet, DATE_PROMPT.format(books='\n'.join(lines)), 3000, 3000, budget)
        for ln in text.replace('```tsv', '').replace('```', '').strip().splitlines():
            f = [x.strip().strip('"').strip() for x in ln.split('\t')]
            if len(f) < 3 or f[0].lower() == 'n': continue
            try: b = todo[idx[int(f[0]) - 1]]
            except (ValueError, IndexError): continue
            ys = [num(x) for x in f[1:3]]
            if all(y is not None and 1450 <= y <= 2030 for y in ys):
                b['printed'] = dict(zip(('from', 'to'), sorted(int(y) for y in ys)), clues=f[3] if len(f) > 3 else '')
                dated += 1
            else: b['printed'] = {}
    return dated


def read_photo(claude, im, budget, wants_second=lambda shelf: False, progress=None, note=lambda shelf: ''):
    """Returns (bands, books). wants_second(shelf): whether that shelf gets an Opus pass for flat and hidden books.
    note(shelf): extra instructions for reading that shelf, such as STICKER_NOTE."""
    bands = find_shelves(claude, im, budget)
    ts = images.tiles(im, bands)
    jobs = [(t, SONNET) for t in ts] + [(t, OPUS) for t in ts if wants_second(t['shelf'])]
    done = [0]

    def one(job):
        t, model = job
        if model == SONNET: text = claude.ask(SONNET, t['jpeg'], READ_PROMPT.format(lines=t['lines'], note=note(t['shelf'])), 8000, 8000, budget)
        else: text = claude.ask(OPUS, t['jpeg'], FLAT_PROMPT.format(lines=t['lines']), 16000, 10000, budget)
        done[0] += 1
        if progress: progress(done[0], len(jobs))
        return _rows(t, text, 'second read' if model == OPUS else 'read')

    with cf.ThreadPoolExecutor(4) as ex: results = list(ex.map(one, jobs))
    first = dedupe([r for res, (t, m) in zip(results, jobs) if m == SONNET for r in res])
    extra = dedupe([r for res, (t, m) in zip(results, jobs) if m == OPUS for r in res])
    for r in extra:                     # add only books the first read missed
        if norm(r['title']) and not any(b['shelf'] == r['shelf'] and similar(b['title'], r['title']) for b in first):
            first.append(r)
    books = sorted(first, key=lambda r: (r['shelf'], r['xc']))
    if progress: progress(len(jobs), len(jobs), 'reading unclear spines up close')
    reread(claude, im, books, budget)
    books = drop_stacked(books)
    for s in {b['shelf'] for b in books}:
        on = [b for b in books if b['shelf'] == s]
        for i, b in enumerate(on, 1): b['pos'], b['of'] = i, len(on)
    for b in books: del b['own']
    return bands, books
