"""Which edition a leatherbound copy is, when its spine names no publisher: its spine, cut from the photo, is compared with
the listing photos of its title's sales (rules.edition_candidates), and the copy is valued only from the sales whose photos
show its own edition. On the bookshop's shelves nearly every such copy is Easton Press or Franklin Library, whose spines
seldom print their name, and each sells in several editions (standard, deluxe, limited) at very different prices.

One Haiku call per copy at high effort, with the spine and up to five sale photos (about 0.1 cent, 6 seconds). In a test of
53 spine-and-photo pairs on the bookshop's shelves it found 5 of the 6 matches and wrongly matched 2 to 4 of the 47 others;
Opus made no wrong matches but costs about 25 times as much. A copy the scan's note, the shelf setting or a shelf sign
assigns to a publisher isn't compared (rules.assess uses that publisher).

The listing photos are read from the store's archive (ARCHIVE), since eBay drops them a few months after a sale, else from
eBay. Each copy gets book['edition_check'] = {compared: [{id, house, price, same, why}], matched: [photo ids]}, kept with the
reading so the rules can be replayed for free.

compare_first does the same for an ordinary copy valued from first-edition sales, with three first-edition photos and a
book-club, facsimile or later edition's (rules.first_candidates), and a prompt about dust jackets: book['first_photos'] =
{compared: [... kind]}. Its answers only add a line to the card (rules.first_photo_line). On 253 such books on the
bookshop's shelves (0.09 cents each) it caught reprint series and other books valued from first editions, but not
book-club copies or facsimiles in the first's jacket, and it didn't bring the results closer to the bookshop's marks."""
import concurrent.futures as cf, json, re, urllib.request
from . import images
from .claude import HAIKU
from .rules import house_of_sale, photo_id
from .store import NotFound, open_store

ARCHIVE = 'sales-photos/'           # sales-photos/<eBay photo id>.jpg, 800 px
EBAY = 'https://i.ebayimg.com/images/g/{}/s-l800.jpg'
PROMPT = """You check books for a bookshop. The first image is one book's spine, cropped from a photo of a shelf. The other
images are photos from sold listings of the same title: they usually show the cover, a box or a whole set, not the spine.

For each sold listing, say whether the book on the shelf is the same edition as the book in that photo: the same publisher
and the same binding design (leather or cloth colour, gilt patterns, lettering, emblems). Different volumes of one uniform
set count as the same edition. Judge by the photos; the listing titles only tell you what the sellers called them. Say
"unsure" when the photos don't show enough to tell.

Reply with JSON only: {"A": {"same": "yes|no|unsure", "why": "under 20 words"}, "B": ...}"""
FIRST_PROMPT = """You check books for a bookshop. The first image is one book's spine, cropped from a photo of a shelf; it may be in a
dust jacket. The other images are photos from sold listings of the same title: they may show the jacket's front, the spine,
the whole book, or a title or copyright page.

For each sold listing, say whether the book on the shelf is the same edition as the book in that photo: the same publisher,
and the same dust-jacket design (or, without a jacket, the same binding: cloth colour, lettering, decoration). Printings of
one edition share its jacket, so they count as the same. A book-club edition, a reissue with a different jacket or binding,
a facsimile (such as the First Edition Library's, in a slipcase) or another publisher's edition does not. Judge by the
photos; the listing titles only tell you what the sellers called them. Compare a jacket with a jacket and a binding with a
binding: when the shelf copy has no jacket and a listing shows only its jacket (or the other way round), their colours say
nothing, so say "unsure" unless something else (the publisher, the lettering on the binding) settles it. Say "unsure" too
when the photos don't show enough to tell, for example a title page only.

Reply with JSON only: {"A": {"same": "yes|no|unsure", "why": "under 20 words"}, "B": ...}"""
_store = None


def sale_photo(sale):
    """The sale's listing photo (JPEG bytes) from the archive, else from eBay; None if neither has it."""
    global _store
    pid = photo_id(sale)
    if not pid: return None
    if _store is None: _store = open_store()
    try: return _store.get_bytes(ARCHIVE + pid + '.jpg')
    except NotFound: pass
    try:
        req = urllib.request.Request(EBAY.format(pid), headers={'User-Agent': 'Mozilla/5.0 (compatible; TomeRaider/0.1)'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read() if r.headers.get_content_type().startswith('image/') else None
    except Exception:
        return None


def spine(im, b):
    """The book's spine with a little of its neighbours, at most 800 px."""
    pad = 0.15 * (b['x1'] - b['x0'])
    crop = im.crop((int(max(0, b['x0'] - pad)), max(0, b['y0'] - 20), int(min(im.size[0], b['x1'] + pad)), min(im.size[1], b['y1'] + 20)))
    crop.thumbnail((800, 800))
    return images.jpeg(crop, 90)


def ask(claude, im, b, sales, budget, fetch=sale_photo, prompt=PROMPT):
    """One Haiku call comparing the spine with the sales' photos (those that can be had): [{id, house, price, same, why}]."""
    have = [(h, photo) for h in sales for photo in [fetch(h)] if photo]
    compared = [dict(id=photo_id(h), house=house_of_sale(h) or '', price=h['price']) for h, _ in have]
    if have:
        parts = ['Shelf spine:', spine(im, b)]
        for letter, (h, photo) in zip('ABCDE', have):
            parts += [f'Sold listing {letter}: "{h["title"]}"', photo]
        text = claude.ask(HAIKU, None, prompt, 8000, 4000, budget, parts=parts, effort='high')
        m = re.search(r'\{.*\}', text, re.S)
        try: reply = json.loads(m.group(0)) if m else {}
        except ValueError: reply = {}
        for letter, c in zip('ABCDE', compared):
            a = reply.get(letter) if isinstance(reply.get(letter), dict) else {}
            c['same'] = str(a.get('same') or 'unsure').lower()
            c['why'] = str(a.get('why') or '').strip()
    return compared


def compare(claude, im, b, sales, budget, fetch=sale_photo):
    """Sets b['edition_check'] for a leatherbound copy: which of its title's sales show its edition."""
    compared = ask(claude, im, b, sales, budget, fetch)
    b['edition_check'] = dict(model=HAIKU, compared=compared, matched=[c['id'] for c in compared if c.get('same') == 'yes'])


def compare_first(claude, im, b, picks, budget, fetch=sale_photo):
    """Sets b['first_photos'] for a copy valued from first editions. picks: [(kind, sale)] from rules.first_candidates."""
    compared = ask(claude, im, b, [h for _, h in picks], budget, fetch, FIRST_PROMPT)
    kinds = {photo_id(h): k for k, h in picks}
    for c in compared: c['kind'] = kinds.get(c['id'], '')
    b['first_photos'] = dict(model=HAIKU, compared=compared)


def run(claude, im, wants, budget, fetch=sale_photo, how=compare):
    """wants: [(book, sales)] from rules.evaluate, compared by how (compare, or compare_first for evaluate's firsts), four
    at a time; returns how many were compared. A copy whose call fails is left uncompared (a leatherbound one is listed to
    check)."""
    def one(bs):
        try: how(claude, im, bs[0], bs[1], budget, fetch); return 1
        except Exception as e:
            print(f"edition check failed for {bs[0].get('title')!r} ({type(e).__name__}: {e})", flush=True)
            return 0
    with cf.ThreadPoolExecutor(4) as ex:
        return sum(ex.map(one, wants))
