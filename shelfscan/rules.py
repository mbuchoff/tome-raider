"""The pull rules from automation plan v4, as fixed code: the same reading and reference data always give the same decision.

Each book gets a decision ('pull', 'not pulled', or 'skip' for books left out silently), the evidence behind it, an estimated
value from the price file when there is one, and the text for the results page."""
import difflib, json, os, re
from urllib.parse import quote_plus
from .refdata import DATA, SIGNED_SALE, median, name_words, same_person, surnames, tokens, words

CATEGORIES = {'thrift': 'Thrift store', 'cheap': 'Cheap bookshop', 'rare': 'Rare bookshop', 'extreme': 'Extremely rare', 'signed': 'Signed shelf'}
PAPERBACK_WORDS = re.compile(r'paperback|\bpb\b|\bpbo\b|mass market|softcover|soft cover|digest', re.I)
# mass-market paperback publishers, used only to recognise paperback sales that don't say "paperback"
PAPERBACK_HOUSES = re.compile(r'\b(signet|ace|bantam|dell|pocket books|pyramid|lion|avon|gold medal|popular library|berkley|fawcett|lancer|'
                              r'belmont|monarch|beacon|corgi|panther|ballantine|penguin|phantom|falcon)\b', re.I)
# a sentence of the notes about signed copies: the bookshop wants one signature line, from the sales, and no other
SIGNED_MENTION = re.compile(r"\bsigned\b|\binscribed\b|autograph|author'?s signature", re.I)
LOT_WORDS = re.compile(r'\blot of\b|\blot\b \d|\bset of\b|facsimile|reprint|book club|\bbce\b', re.I)
# a listing of several different books ("4 Franklin Library Leather Paine ... Prentiss Plays ..."): its price is no one book's
MULTI_BOOK = re.compile(r'\blot of\b|\blot\b \d|^\W*(?:[2-9]|[1-4]\d)\s+(?:franklin|easton|folio|heritage)\b', re.I)
# the same, worded "3 Books" or "Easton bundle", where it doesn't read as a set of one work (the set rules handle those)
BUNDLE = re.compile(r'\bbundle\b|\b(?:[2-9]|[12]\d)[\s-]+(?:different\s+)?(?:books?|novels|titles|hardcovers)\b(?!\s+in\s+(?:1|one)\b)', re.I)
# the First Edition Library ("FEL") prints facsimiles of first editions: its price is no real copy's, even with no other
# sale; "FAC/FEL dust jacket" is a real copy in a facsimile jacket
FACSIMILE_LIBRARY = re.compile(r'first editions? lib(?:rary)?\b|\bfel\b(?!\s*(?:dust\s*)?(?:jacket|dj|d/j)\b)', re.I)
# a copy whose spine states a later edition or printing ("6th Edition"): first-edition sales say nothing about it
LATER_EDITION = re.compile(r'\b(?:[2-9](?:nd|rd|th)|[1-9]\d(?:st|nd|rd|th)|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|later)'
                           r'\s+(?:edition|printing|impression)\b', re.I)
# a story collection whose edition is the point, like a fine-press one: limited, numbered or lettered
LIMITED_MARK = re.compile(r"\blimited\b|\bnumbered\b|(?<!-)(?<!\w )\blettered\b", re.I)      # not "red-lettered"
# retold, abridged and children's versions of a classic: sales of the original work say nothing about them
ADAPTATION = re.compile(r'for boys and girls|\bretold\b|\babridged\b|adapted (for|by)|junior library|classics illustrated|'
                        r'told to the children|\bsimplified\b|for young readers|children\'s edition', re.I)
FIRST_EDITION_MARK = re.compile(r'first edition|1st edition|first printing', re.I)
# a sale claiming the first edition: what a later printing must not be valued from (the bookshop, 28 October 2026)
FIRST_EDITION_SALE = re.compile(r'\b(?:first|1st)\s+(?:(?:true|stated|american|english|british|uk|u\.?s\.?|trade|retail|hardcover|hardback)\s+){0,2}'
                                r'(?:edition|eds?\b|printing|print\b|impression)|1st/1st', re.I)
# a listing of a whole set, where the copy on the shelf is usually one volume or a few
SET_LISTING = re.compile(r'\b(?:\d+|two|three|four|five|six|seven|eight|nine|ten|twelve)[\s-]*(?:vol(?:ume)?s?\b|v\b|book set)|\bcomplete set\b|'
                         r'\bset\b|\ball (?:first editions|volumes)\b|\bvols\b|(?<![\d/.])\b(?:[2-9]|[1-4]\d)\s+(?:[\w-]+\s+){1,3}vol(?:ume)?s\b|'
                         r'(?<!signed )\b(?:first|1st) editions\b(?!\s+(?:library|society|club))|\btrilogy\b|\b(?:book|novel) series\b', re.I)
# ("Signed First Editions" is an Easton series, and the First Editions Library prints facsimiles: neither is a set)
# a work the notes say first came in several volumes ("Six vols"): its listings that say "complete" or span years
# ("1948-1953") are whole sets too, though a range alone usually dates one book ("The Hobbit 1937-1966")
MULTI_VOLUME = re.compile(r'\b(?:\d+|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+vol', re.I)
WHOLE_WORK = re.compile(r'\bcomplete\b|(?<!\d)(?:1[5-9]\d\d|20[0-2]\d)\s*[-–—]\s*(?:\d{2}|\d{4})(?!\d)', re.I)
VISIBLY_LEATHER = re.compile(r'\bleather', re.I)
VALUABLE_FIRST = 500        # first editions selling this high are never a later printing's value without evidence on the copy
# signs on an old spine that it may be a special copy, for lesser-known leads: illustrations, translations, small presses
LEAD_CUE = re.compile(r'illustrat|pictured by|plates|drawings|engrav|woodcut|decorations by|translated|\bpress\b|society|'
                      r'privately|limited|pictorial', re.I)
RARE_SHELVES = ('rare', 'extreme')
# shelves that are priced high already: no minimum value, every candidate listed in check order, most likely value first
CHECK_ORDER = ('extreme', 'signed')
# the reader writes "signed sticker" in marks for a Signed or Autographed sticker or label on the spine or jacket
STICKER = re.compile(r'signed sticker|autograph\w* (sticker|label)|(sticker|label)[^,;]*\b(signed|autograph)', re.I)
ROMAN_YEAR = re.compile(r'\bM{1,2}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\b')
ARABIC_YEAR = re.compile(r'(?<!\d)(1[5-9]\d\d|20[0-2]\d)(?!\d)')


def roman(s):
    v = {'M': 1000, 'D': 500, 'C': 100, 'L': 50, 'X': 10, 'V': 5, 'I': 1}
    total = 0
    for a, b in zip(s, s[1:] + ' '):
        total += -v[a] if b != ' ' and v[a] < v[b] else v[a]
    return total


YEAR_RANGE = re.compile(r'(?<!\d)(1[5-9]\d\d|20[0-2]\d)\s*[-–—/]\s*(\d{2,4})(?!\d)')


def spine_year(spine_text, title=''):
    """A printing year on the spine (MCMXXXV or 1935), or None. Ranges ('1934-1998') and years in the title ('1776') are
    subjects, not printing dates; with several years, the latest is the earliest the copy can have been printed."""
    s = YEAR_RANGE.sub(' ', spine_text or '')
    years = [roman(m) for m in ROMAN_YEAR.findall(s) if len(m) >= 4] + [int(y) for y in ARABIC_YEAR.findall(s)]
    years = [y for y in years if 1450 <= y <= 2030 and str(y) not in (title or '')]
    return max(years) if years else None


def listing_year(listing, title=''):
    """The earliest year a sold listing names, back to the first printed books (a listing's own year leaves out the 1400s).
    A limitation number ('#1440', '804/1400'), a span ('1478-1978'), a century ('1400s') or a year in the copy's own title
    ('1776') is no printing date."""
    s = re.sub(r'(?<!\d)1[4-9]\d\d\s*[-–—/]\s*\d{2,4}(?!\d)|[#/]\s*\d+', ' ', listing or '')
    years = [y for y in re.findall(r'(?<!\d)(1[4-9]\d\d|20[0-2]\d)(?![\ds])', s) if y not in (title or '')]
    return int(min(years)) if years else None


def clean(s):
    s = (s or '').strip()
    return '' if not s or 'unreadable' in s.lower() else s


def unreadable(b):
    """No title and no author could be read from the spine."""
    return not clean(b.get('title')) and not clean(b.get('author'))


def fmt_money(v):
    return f'${v:,.0f}' if v is not None else 'no price data'


def links(author, title, imprint):
    q = ' '.join(x for x in (re.sub(r'[?…]|\.\.\.', '', author), re.sub(r'[?…]|\.\.\.', '', title)) if x).strip()
    return {
        'terapeak': 'https://www.ebay.com/sh/research?marketplace=EBAY-US&tabName=SOLD&dayRange=1095&keywords=' + quote_plus(q),
        'ebay_sold': 'https://www.ebay.com/sch/i.html?LH_Sold=1&LH_Complete=1&_nkw=' + quote_plus(q),
        'abebooks': 'https://www.abebooks.com/servlet/SearchResults?sortby=17&an=' + quote_plus(author) + '&tn=' + quote_plus(title),
    }


# leather-bound publishers whose sales value only their own copies: an Easton Press Fahrenheit 451 says nothing about a
# Ballantine one, and the other way round
LEATHER = {'Easton Press': re.compile(r'\beaston\b(?!\s+ellis)', re.I), 'Franklin Library': re.compile(r'\bfranklin\s+(library|mint)\b', re.I)}
# the bookshop's specialty publishers, whose sales also value only their own copies (Modern Library, Folio Society...): a
# list in rules.json, so adding one is a line. Easton and Franklin come first: "Easton Press Kelmscott Edition" is Easton's.
SPECIALTY = {h: re.compile(rx, re.I) for h, rx in json.load(open(os.path.join(DATA, 'rules.json'))).get('specialty_publishers', {}).items()}
HOUSES = {**LEATHER, **SPECIALTY}


def special_house(text):
    """The publisher whose sales value only its own copies ('Easton Press', 'Modern Library'...) or None, from a copy's
    spine, imprint and foot or a sale's listing title."""
    return next((h for h, rx in HOUSES.items() if rx.search(text or '')), None)


def house_of_sale(h):
    return h.get('publisher') if h.get('publisher') in HOUSES else special_house(h['title'])


ANY_LEATHER = 'leather'      # a copy that is visibly leatherbound but names no publisher


def same_house(sales, house):
    """The sales from the copy's own publisher when it is Easton Press, Franklin Library or a specialty publisher, none of
    theirs for an ordinary copy, and any for a copy that is visibly leatherbound but names no publisher."""
    if house == ANY_LEATHER: return list(sales)
    return [h for h in sales if house_of_sale(h) == house]


# an ISBN or a barcode on a spine: printed 1966 or later (the SBN came in 1966, the ISBN in 1970)
MODERN_MARK = re.compile(r'\bI?SBN\b|\bbar ?code\b|\bUPC\b', re.I)


def era(look, year, printed=None, modern=False):
    """The years a copy's own edition could carry: from a date on its spine, else the years its spine's colours and details
    say it was printed (reader.date_copies), else the look the reader gave it; never before 1966 with an ISBN or barcode."""
    if year: lo, hi = year - 5, year + 5
    elif printed and printed.get('from'): lo, hi = printed['from'] - 5, printed['to'] + 5
    else: lo, hi = {'antique': (1450, 1930), 'old': (1915, 1975), 'modern': (1960, 2100)}.get(look, (1450, 2100))
    if modern: lo = max(lo, 1966); hi = max(hi, lo)
    return lo, hi


def imprint_word(w):
    """A publisher's name word, with a final s dropped: 'Harpers' is Harper & Brothers, 'Scribners' Scribner."""
    return w[:-1] if len(w) > 4 and w.endswith('s') else w


def first_publishers(notes):
    """The words naming the first edition's publishers in the notes ('1932, Chatto & Windus, London; US: Doubleday'):
    cities, years and little words left out."""
    t = re.sub(r'\b(london|new york|boston|philadelphia|paris|chicago|edinburgh|toronto|garden city|indianapolis|us|uk)\b', ' ',
               (notes or {}).get('first') or '', flags=re.I)
    return {imprint_word(w) for w in tokens(t) if len(w) > 2 and not w.isdigit() and w not in ('and', 'the', 'company', 'press', 'publishers', 'inc', 'ltd', 'sons')}


def same_imprint(imprint, notes):
    """None when the copy's publisher or the first edition's is unknown; else whether they share a name ('Knopf')."""
    have = {imprint_word(w) for w in tokens(imprint) if len(w) > 2 and w not in ('the', 'and', 'company', 'press', 'publishers', 'inc', 'ltd', 'sons', 'books')}
    first = first_publishers(notes)
    if not have or not first: return None
    return bool(have & first)


def is_signed(h):
    """A signed sale: its listing says so, or its file's Signed column does (Easton Press and Franklin Library)."""
    return bool(h.get('signed')) or bool(SIGNED_SALE.search(h['title']))


def without_signatures(notes):
    """The notes with any sentence about signed copies left out."""
    out = dict(notes)
    for k in ('first', 'points', 'watch'):
        if isinstance(out.get(k), str):
            out[k] = ' '.join(x for x in re.split(r'(?<=[.;])\s+', out[k]) if not SIGNED_MENTION.search(x)).strip()
    return out


def several_books(listing):
    return bool(MULTI_BOOK.search(listing) or (BUNDLE.search(listing) and not SET_LISTING.search(listing)))


def relevant_comps(hits, fmt, signed_ok, house=None):
    """Narrow sold listings to ones like this copy: the same leather publisher or none, same format, unsigned unless
    signatures count, no lots or reprints. Sales from the paperback and book-club list are of their kind whatever they say."""
    hits = [h for h in same_house(hits, house) if not several_books(h['title']) and not FACSIMILE_LIBRARY.search(h['title'])]
    hits = [h for h in hits if h.get('kind') or not LOT_WORDS.search(h['title'])] or hits
    if fmt == 'paperback':
        hits = [h for h in hits if h.get('kind') == 'paperback' or PAPERBACK_WORDS.search(h['title']) or PAPERBACK_HOUSES.search(h['title'])]
    else:
        hc = [h for h in hits if not PAPERBACK_WORDS.search(h['title'])]
        hits = hc or hits
    only_signed = bool(hits) and all(is_signed(h) for h in hits)
    if not signed_ok:
        hits = [h for h in hits if not is_signed(h)]
    return hits, only_signed


def whitelist_match(ref, author, title, imprint, look, text):
    found = []
    for e in ref.whitelist:
        if e['_t'] and not e['_t'].search(title or ''): continue
        if e['_a'] and author and not e['_a'].search(author): continue
        if e['_a'] and not author and not e['_t']: continue
        if not e['_t'] and not (e['_p'] and e['_p'].search(imprint or text)): continue   # publisher-only entries (Penguin crime)
        if e.get('look') and look not in ('old', 'antique'): continue
        if e.get('appearance') and not re.search(e['appearance'], text, re.I): continue      # e.g. green Penguin crime covers
        if e.get('exclude') and re.search(e['exclude'], text, re.I): continue
        pub_ok = not e['_p'] or not imprint or bool(e['_p'].search(imprint))
        if pub_ok: found.append(e)
    return found


def assess(b, category, ref, minimum):
    author, title, imprint = clean(b.get('author')), clean(b.get('title')), clean(b.get('imprint'))
    read_imprint = imprint          # as read on the spine, before the foot's or the notes' guesses fill it in
    marks, look, fmt = (b.get('marks') or ''), (b.get('look') or '').lower(), (b.get('format') or '').lower()
    foot = b.get('foot') or {}                  # the close-up of the spine's foot, read for pulls and leads
    foot_text = ' '.join(clean(foot.get(k)) for k in ('foot_text', 'logo', 'imprint', 'reprint'))
    if clean(foot.get('imprint')) and (not imprint or '?' in imprint): imprint = clean(foot['imprint'])
    text = ' '.join([author, title, imprint, marks, b.get('spine_text') or '', b.get('appearance') or '', foot_text])
    out = dict(decision='skip', reason='', signals=[], value=None, comps=[], quick=[], why='', edition='', basis='', strength=0)
    if b.get('notes'): out['notes'] = without_signatures(b['notes'])     # written once by the notes call, kept with the reading
    notes = b.get('notes') or {}
    out['checks'] = []          # check lines written once per book: a possible signature, a possible first edition, a partial set
    # a second look for a book with no author, or only one name (a surname or a publisher): the notes call identifies it
    nw = name_words(author)              # "?" or "(Cervantes?)" can leave no name at all
    lone = len(nw) < 2 and not (nw and nw[0].lower().strip('.').replace('ë', 'e') in ref.single_names)
    if (not author or lone) and clean(notes.get('author')) and notes.get('confidence') in ('high', 'medium'):
        author = clean(notes['author'])
        out['identified'] = author
        out['signals'].append(f'author identified by the notes as {author}')
    if (not imprint or '?' in imprint) and clean(notes.get('publisher')): imprint = clean(notes['publisher'])
    if unreadable(b):
        if category == 'rare' and look == 'antique' and b.get('legibility') != 'unreadable':
            out['decision'], out['reason'] = 'not pulled', 'title unreadable; an antique on a rare shelf, so check it in person'
            return out
        out['reason'] = 'unreadable'
        return out
    year = spine_year(b.get('spine_text'), title)
    if year and year < 1970:            # a date printed on the spine beats the model's guess at how old the book looks
        look = 'antique' if year < 1925 else 'old'
    old = look in ('old', 'antique')
    signed_shelf = category == 'signed'
    check_order = category in CHECK_ORDER
    sticker = STICKER.search(marks)

    # paperbacks are skipped silently unless whitelisted, sold as first-edition paperbacks on the bookshop's paperback and
    # book-club list, or by an always-check author; an unclear format is treated as a hardcover
    wl = whitelist_match(ref, author, title, imprint, look, text) if fmt == 'paperback' else []
    listed_pb = ref.comps(author, title, old, kind='paperback')[0] if fmt == 'paperback' else []
    if fmt == 'paperback' and not wl and not listed_pb and not ref.always_pb.search(author + ' ' + b.get('spine_text', '')):
        out['reason'] = 'paperback not on the whitelist'
        return out

    # story collections, anthologies and omnibuses are treated like paperbacks: left out unless whitelisted. Illustrated and
    # fine-press editions stay, since their value is the edition, not the texts.
    kind, how = ref.collections.kind(b)
    coll_ok = ref.collections.whitelisted(b) if kind else None
    if kind and not coll_ok:
        imp0 = ref.rx['collectible_imprints'].search(' '.join([imprint, b.get('spine_text') or '', foot_text]))
        if not (ref.rx['illustrators'].search(text) or imp0 or LIMITED_MARK.search(marks)):
            out['reason'] = 'collection or anthology not on the whitelist'
            out['collection'] = dict(kind=kind, how=how)
            return out

    sig = out['signals']
    if coll_ok: sig.append(f"{'omnibus' if kind == 'omnibus' else 'collection'} on the whitelist")
    if wl:
        e = wl[0]
        out['edition'] = '; '.join(dict.fromkeys(x['edition'] for x in wl))
        sig.append('paperback whitelist')
        out['quick'].append(f"Is it the {out['edition']}?")
        if e.get('note'): out['quick'].append(e['note'])
    if listed_pb: sig.append('on your paperback and book-club list')

    # a book about a person, by someone else (Mark Schorer's Sinclair Lewis): valued only from sales of the book itself
    subject = clean(notes.get('about')) or (title if ref.is_author_name(title) else '')
    about = bool(subject) and not (author and same_person(subject, author, ref.single_names))
    if about: sig.append(f'about {subject}, so valued only from its own sales')

    # evidence from the reference data
    adaptation = ADAPTATION.search(' '.join([title, marks, b.get('spine_text') or '']))
    hits, author_known = ref.comps(author, title, old, about=about) if not adaptation else ([], bool(author))
    if listed_pb: hits = listed_pb + hits
    # a publisher whose sales value only its own copies must be read as text, on the spine or at its foot: the foot
    # close-up's guess from an emblem ("gold circular emblem": Easton Press) has been wrong
    house = special_house(' '.join([read_imprint, b.get('spine_text') or '', clean(foot.get('foot_text'))]))
    if not house and VISIBLY_LEATHER.search(' '.join([marks, b.get('appearance') or ''])): house = ANY_LEATHER
    # a book-club copy is valued only from the book-club sales on the bookshop's paperback and book-club list, and left out
    # when it has none there. Subscription publishers such as Franklin Library and Easton Press can read as clubs, but they
    # count as collectible.
    imp = ref.rx['collectible_imprints'].search(' '.join([imprint, b.get('spine_text') or '', foot_text]))
    bc_text = ' '.join([imprint, marks, b.get('spine_text') or '', foot_text])
    club = ('book-club copy (club logo at the foot of the spine)' if (foot.get('book_club') or '').lower().startswith('yes') and not imp
            else 'book-club copy' if ref.rx['book_club_sure'].search(bc_text) and '?' not in marks else '')
    sells = bool(hits)                  # the title's ordinary sales: a book-club copy of a title that sells is said to be left out
    if club: hits, author_known = ref.comps(author, title, old, about=about, kind='book club')
    signed_ok = signed_shelf or bool(re.search(r'\bsigned\b', marks, re.I))
    comps, only_signed = relevant_comps(hits, fmt, signed_ok, house)
    later = LATER_EDITION.search(' '.join([b.get('spine_text') or '', marks, foot_text]))
    def first_of_later(h): return bool(later) and bool(FIRST_EDITION_SALE.search(h['title']))
    if later and any(map(first_of_later, comps)):
        comps = [h for h in comps if not first_of_later(h)]
        sig.append(f'the spine says {later.group(0)}, so first-edition sales are left out')

    # other editions: a sale dated outside the copy's own years, a first edition from another publisher than the copy's, or
    # a valuable first-edition sale ($500 and up) with nothing on the copy to say it is one, values a later printing at nothing
    first_year = min((int(y) for y in re.findall(r'(?<!\d)(1[5-9]\d\d|20[0-2]\d)(?!\d)', notes.get('first') or '')), default=None)
    # a sale dated long before the work was first published is another book with its title (a leaf of a 15th-century
    # book for a 20th-century novel): the bookshop, "year on sold listing should have told you it was a different book"
    older = [h for h in comps if first_year and (listing_year(h['title'], title) or 9999) < first_year - 10]
    if older:
        comps = [h for h in comps if h not in older]
        sig.append(f"first published {first_year}, so {len(older)} sale{'s' if len(older) != 1 else ''} dated long before then left out")
    printed = b.get('printed') if not year else None
    modern = MODERN_MARK.search(' '.join([b.get('spine_text') or '', marks, foot_text]))
    lo, hi = era(look, year, printed, bool(modern))
    # the bookshop: "versions will come out many years apart ... match to sold listings with the same year". With the copy's
    # own years known, a title whose sales are dated is valued only from sales dated to those years: an undated "deluxe
    # vellum" listing says nothing about a plain 1930s reprint
    out_of_years = [h for h in comps if h.get('year') and not lo <= h['year'] <= hi]
    own_years = bool(year) or bool(printed and printed.get('from')) or bool(modern)
    undated_out = [h for h in comps if not h.get('year')] if own_years and any(h.get('year') for h in comps) else []
    if own_years and (out_of_years or undated_out):
        n = len(out_of_years) + len(undated_out)
        left = f"{n} sale{'s' if n != 1 else ''} not dated to those years left out"
        if year: sig.append(f'the spine says {year}, so {left}')
        else:
            when = (f"{printed['from']}" + (f"–{printed['to']}" if printed['to'] != printed['from'] else '')) if printed and printed.get('from') else '1966 or later'
            why = clean(printed.get('clues')) if printed and printed.get('from') else f'{modern.group(0)} on the spine'
            sig.append(f"looks printed {when}{f' ({why})' if why else ''}, so {left}")
    shows_first = bool(FIRST_EDITION_SALE.search(marks))
    # a Modern Library, Folio or Easton copy is valued only from its own publisher's sales: their "1st edition" is that
    # publisher's first printing, never the work's first edition from another house
    pub = same_imprint(imprint, notes) if house in (None, ANY_LEATHER) else True
    evidence = shows_first or bool(pub and year)        # something on the copy says it may be the first edition
    def other_edition(h):
        if h.get('year') and not lo <= h['year'] <= hi: return True
        if undated_out and not h.get('year'): return True
        if FIRST_EDITION_SALE.search(h['title']):
            if not h.get('year') and first_year and not lo <= first_year <= hi: return True     # an undated first: the notes' year
            return pub is False or (h['price'] >= VALUABLE_FIRST and not evidence)
        return False
    # partial sets: single volumes value a volume; where the title sold only as whole sets, the copy is still listed, with
    # the sets' price, from sets of its own edition, as a ceiling it won't reach (the bookshop: "worth much less than a
    # complete set")
    multi = bool(MULTI_VOLUME.search(notes.get('first') or ''))
    def is_set(h): return bool(SET_LISTING.search(h['title']) or (multi and WHOLE_WORK.search(h['title'])))
    whole_sets = [h for h in comps if is_set(h)] if not SET_LISTING.search(title) else []   # "A Set of Six" is one book
    only_sets = bool(whole_sets) and len(whole_sets) == len(comps)
    own_sets = [h for h in whole_sets if not other_edition(h)]
    if whole_sets and not only_sets: comps = [h for h in comps if h not in whole_sets]
    elif own_sets:
        out['checks'].append(f"Complete sets sold for a median of {fmt_money(median([h['price'] for h in own_sets]))}; a partial set is worth much less")
    elsewhere = [h for h in comps if other_edition(h)]
    comps = [h for h in comps if not other_edition(h)]
    if comps:
        out['value'] = median([h['price'] for h in comps])
        out['comps'] = sorted(comps, key=lambda h: -h['price'])[:4]
        out['basis'] = ('complete sets of this title' if only_sets else 'sales of this title') if not club else 'book-club sales of this title'
        sig.append(f"{len(comps)} {'book-club ' if club else ''}sale{'s' if len(comps) != 1 else ''} of this title" + (' by this author' if author_known else '')
                   + (f", median {fmt_money(out['value'])}" if len(comps) > 1 else f" at {fmt_money(out['value'])}"))
    elif only_signed and hits:
        sig.append('title sells only when signed')
    signed_copy = signed_shelf or (check_order and bool(sticker))
    # a signature can't be seen on a spine: say once what signed copies fetch, when they fetch much more (the bookshop's choice)
    signed = [h for h in same_house(hits, house) if is_signed(h) and not LOT_WORDS.search(h['title']) and not other_edition(h)
              and not first_of_later(h)]
    if signed and not signed_copy and not signed_ok:
        ms = median([h['price'] for h in signed])
        if out['value'] is None or ms >= 1.5 * out['value']:
            out['checks'].append(f'Check for a signature: signed copies sold for a median of {fmt_money(ms)}')
    # a book first published well after the latest year in its author's sales is likely not worth pulling (the bookshop,
    # 29 October): with no sales of its own title, its author's other books and lists don't count for it. The years are
    # late_book_years in rules.json; null leaves the rule off.
    late_n, late = ref.rules.get('late_book_years'), None
    if late_n is not None and not comps and first_year and author:
        years = [h['year'] for h in ref.author_sales(author) if h.get('year')]
        if years and first_year > max(years) + late_n: late = max(years)
    if late: sig.append(f"first published {first_year}, {first_year - late} years after the latest year in {author}'s sales ({late})")
    freq = ref.find_frequent(author) if not late else None
    if freq: sig.append(f'frequent author in the sales ({freq[0]})')
    watch = ref.find_watch(author) if not late else None
    if watch: sig.append(f'watch-list author ({watch})')
    if ref.always_pb.search(author) and fmt == 'paperback': sig.append('always-check paperback author')
    ill = ref.rx['illustrators'].search(text)
    if ill: sig.append(f'illustrator ({ill.group(0).title()})')
    if imp: sig.append(f'collectible publisher ({imp.group(0).title()})')
    mk = ref.rx['collectible_marks'].search(marks)
    if mk and look == 'modern' and FIRST_EDITION_MARK.fullmatch(mk.group(0)):
        mk = None           # a "first edition" note on a modern spine is usually the model's guess, or a common trade first
    if sticker: sig.insert(0, 'signed sticker on the spine')
    if mk and not (sticker and mk.group(0).lower() == 'signed'): sig.append(f'marked {mk.group(0).lower()}')
    if adaptation: sig.append(f'retold or abridged edition ({adaptation.group(0).lower()})')
    if year and year < 1970: sig.append(f'dated {year} on the spine')
    elif old: sig.append(f'{look} look')
    hard = bool(ill or imp or mk or look == 'antique' or wl or (year and year < 1970))
    matched = bool(comps)

    def no(reason):
        out['decision'], out['reason'] = 'not pulled', reason
        return out

    # on shelves read in check order, an author with several sales in the price file is enough to list the book
    ac = same_house(ref.author_comps(author), house) if check_order and not about and not late else []     # about someone: its own sales only
    any_signal = bool(comps or freq or watch or ill or imp or mk or wl or len(ac) >= 3)
    # on rare shelves, an old book with no evidence in the price file can still be a lesser-known lead: an old book whose spine
    # shows illustrations, a translation or a small press, or on a rare shelf any antique. On shelves read in check order
    # (antique shops), age alone isn't enough.
    cue = LEAD_CUE.search(text) if old else None
    lead_only = (not any_signal and not signed_shelf and category in RARE_SHELVES and bool(title)
                 and (bool(cue) or (look == 'antique' and not check_order)))
    # a copy of a title whose other editions sell is listed to check: on rare and antique shelves any hardcover, elsewhere
    # only a quality one (the bookshop: "only hardcover reprints of high quality or collectors editions", never paperbacks)
    first_check = (not comps and bool(elsewhere) and fmt != 'paperback' and not about
                   and (hard or house is not None or category in RARE_SHELVES or check_order)
                   and median([h['price'] for h in elsewhere]) >= minimum)
    if first_check:
        out['checks'].append(f"Other editions sold for a median of {fmt_money(median([h['price'] for h in elsewhere]))}: check whether this is one"
                             + (f" (first edition: {notes['first']})" if clean(notes.get('first')) else ''))
        if not check_order: lead_only = True
        any_signal = True
    if not any_signal and not signed_shelf and not lead_only:
        if club and sells: return no(club)
        out['reason'] = 'no signal'
        return out
    if cue: sig.append(f'"{cue.group(0).lower()}" on the spine')

    # exclusions
    if club and not (comps or first_check): return no(club)
    if ref.rx['book_club_possible'].search(marks): out['quick'].append('Possibly a book-club copy')
    rp = ref.rx['reprint_publishers'].search(' '.join([imprint, b.get('spine_text') or '', foot_text]))
    if rp: return no(f'modern reprint ({rp.group(0).title()})')
    if re.search(r'leather', marks, re.I) and not old and not imp:       # modern, or an age the model couldn't call
        return no('modern decorative leather reprint')
    if adaptation and not (ill or imp or mk):
        return no('retold or abridged edition, without an illustrator or collectible publisher')
    if ref.rx['false_friend_authors'].search(author + ' ' + title) and not signed_copy and (lead_only or (not (matched and old) and not (look == 'antique' and hard))):
        return no('common author whose old copies are usually ordinary')
    if re.search(r'ex-?lib|library (label|stamp|sticker|mark)', marks, re.I):
        out['quick'].append('Ex-library copy')

    def estimate(sales, basis, rough=False):
        # a rough guide (the author's other books) takes the lower quartile: one famous title shouldn't lift every book by its author
        prices = sorted(h['price'] for h in sales)
        out['value'], out['basis'] = (prices[len(prices) // 4] if rough else median(prices)), basis
        out['comps'] = sorted(sales, key=lambda h: -h['price'])[:4]
        sig.insert(0, f"{basis}: " + (f"1 sold, at {fmt_money(out['value'])}" if len(sales) == 1 else
                                      f"{len(sales)} sold, {'lower quartile' if rough else 'median'} {fmt_money(out['value'])}"))

    # the category's rule
    val = out['value']
    if check_order:
        # no minimum: every candidate is listed, ordered by the likeliest value the evidence gives
        st = [h for h in comps if is_signed(h)] if signed_copy else []
        sc = same_house(ref.signed_comps(author), house) if signed_copy and not st and not about else []
        if st: estimate(st, 'signed copies of this title')
        elif sc and (val is None or median([h['price'] for h in sc]) >= val): estimate(sc, 'signed copies by this author')
        elif comps:
            if signed_copy: out['basis'] = 'unsigned copies of this title; a signature adds to it'
        elif len(ac) >= 3: estimate(ac, "this author's other books (a rough guide)", rough=True)
        ok = True
    elif first_check: ok = True                 # a possible first edition or collectable printing: a lead to check
    elif val is not None and val < minimum and not wl:
        return no(f'matching sales median {fmt_money(val)}, under the {fmt_money(minimum)} minimum')
    elif only_signed and not comps and not (old and hard) and not wl:
        return no('this title sold for over $50 only when signed')
    elif not old and not matched and not (ill or imp or mk or wl) and category != 'extreme':
        if category != 'rare': return no('modern copy by a collected author, with no sales of this title')
        ok, lead_only = True, True          # on a rare shelf, a lead to check rather than a pull
        sig.append('no sales of this title')
    elif not old and matched and len(comps) < 2 and category in ('thrift', 'cheap') and not (imp or mk or wl or listed_pb):
        return no('only one sale of this title, and this looks like a later modern copy')
    elif category == 'thrift':
        ok = bool(wl or (listed_pb and matched)) or (bool(matched or freq or watch) and old and hard) or (matched and author_known and old and val is not None)
    elif category == 'cheap':
        ok = bool(wl or (listed_pb and matched)) or ((matched or freq or watch or ill or imp) and old) or bool(imp or mk) or (matched and author_known and fmt != 'paperback')
    else:   # rare: everything plausible, ranked by value
        ok = True
    if not ok:
        if matched and not old: return no('title sells, but this looks like a later modern copy')
        return no('not enough evidence for this shelf category')

    out['decision'] = 'lead' if lead_only and not check_order else 'pull'
    out['strength'] = (4 * bool(sticker) + 3 * bool(imp or mk) + 2 * bool(ill) + {'antique': 2, 'old': 1}.get(look, 0)
                       + bool(freq or watch) + bool(matched))
    reasons = [s for s in sig if not s.endswith(' look') and not s.startswith('dated ')][:3] + [s for s in sig if s.startswith('dated ')]
    if old and not (year and year < 1970) and not check_order: reasons.append(f'{look} look')
    if lead_only and not matched: reasons.append('no matching sales in the price file')
    if signed_shelf and not any('signed' in s for s in reasons): reasons.insert(0, 'on the signed shelf, so valued by its signature')
    out['why'] = '; '.join(reasons)
    return out


def rank_key(item):
    """Likeliest value first; books with no price evidence after those, strongest evidence (a signed sticker, a collectible
    publisher or mark, an illustrator, age) first."""
    a = item['assessment']
    return (a['value'] is None, -(a['value'] or 0), -a.get('strength', 0), -(item['book'].get('confidence') or 0))


def describe(b):
    def nice(s): return clean(s).replace('...', '…')
    t, a = nice(b.get('title')), nice(b.get('author'))
    return (f'{t}' if t else '(title unreadable)') + (f', {a}' if a else '')


def bare_title(t):
    return re.sub(r'\s+', ' ', re.sub(r"[^a-z0-9 ]", ' ', re.sub(r'\(.*?\)|\?|\.\.\.|…', ' ', (t or '').lower()))).strip()


def same_work(x, y):
    """Two entries for copies of the same title by the same author (or with the author unread), on shelves of one category."""
    def main(t): return bare_title(re.split(r'\s*[:;]', t)[0])
    ta, tb = clean(x['book'].get('title')), clean(y['book'].get('title'))
    a, b = bare_title(ta), bare_title(tb)
    if not a or not b or x['category'] != y['category']: return False
    if a != b and main(ta) != main(tb) and difflib.SequenceMatcher(None, a, b).ratio() < 0.85: return False
    sa, sb = set(surnames(clean(x['book'].get('author')))), set(surnames(clean(y['book'].get('author'))))
    return not sa or not sb or bool(sa & sb)


def copy_key(item):
    """The copy most worth looking at first: hard signals, then age, an early date on the spine, a jacket, then value."""
    a, b = item['assessment'], item['book']
    year = spine_year(b.get('spine_text'), b.get('title')) or 9999
    hard = any(s.startswith(('illustrator', 'collectible publisher', 'marked', 'paperback whitelist')) for s in a['signals'])
    return (not hard, {'antique': 0, 'old': 1}.get((b.get('look') or '').lower(), 2), year, b.get('jacket') != 'yes',
            -(a['value'] or 0), -(b.get('confidence') or 0))


def group_copies(items):
    """Folds several copies of one title into a single entry, best copy first, with the others in item['copies']."""
    groups = []
    for x in items:
        g = next((g for g in groups if any(same_work(y, x) for y in g)), None)
        if g is None: groups.append([x])
        else: g.append(x)
    out = []
    for g in groups:
        g.sort(key=copy_key)
        g[0]['copies'] = g[1:]
        out.append(g[0])
    return out


def evaluate(books, shelf_category, ref, minimum_for):
    """books: reader output. shelf_category: function shelf -> category. minimum_for: function category -> $.
    Returns (pulls, leads, not_pulled, skipped). Pulls and leads are numbered in one sequence, pulls first; several copies
    of one title share an entry and its number."""
    pulls, leads, not_pulled, skipped = [], [], [], {'paperback not on the whitelist': 0, 'unreadable': 0, 'no signal': 0}
    for b in books:
        cat = shelf_category(b['shelf'])
        a = assess(b, cat, ref, minimum_for(cat))
        item = {'book': b, 'assessment': a, 'category': cat, 'label': describe(b),
                'links': links(clean(b.get('author')), clean(b.get('title')), clean(b.get('imprint')))}
        if a['decision'] == 'pull': pulls.append(item)
        elif a['decision'] == 'lead': leads.append(item)
        elif a['decision'] == 'not pulled': not_pulled.append(item)
        else: skipped[a['reason']] = skipped.get(a['reason'], 0) + 1
    pulls = sorted(group_copies(pulls), key=lambda x: min(rank_key(c) for c in [x] + x['copies']))
    leads = sorted(group_copies(leads), key=lambda x: (x['book'].get('look') != 'antique', -(x['book'].get('confidence') or 0)))
    for i, p in enumerate(pulls + leads, 1): p['n'] = i
    not_pulled.sort(key=lambda x: (x['book']['shelf'], x['book']['pos']))
    return pulls, leads, not_pulled, skipped
