"""The scan's note, applied to the books read: which ones it says are signed, not signed or book-club copies ("Top left shelf 1
is signed up until the Asimov books, which aren't signed"; the bookshop also wants book-club sections named this way), and
which are one publisher's ("the third shelf is Easton Press"), which the rules use when a spine names none.

One text-only Sonnet call per note, after every photo of the scan is read and before the rules run, since a note can name a
section on any photo (about a cent: the titles and authors of a scan's books). It sees the note and each photo's books shelf
by shelf, left to right, and names the books the note covers. Each named book gets book['from_note'], which the rules read
(rules.assess), or book['publisher_hint'] for a publisher, and the photo's results say what the note was taken to mean, so
the user can check it."""
import json, re
from .claude import SONNET
from .rules import clean, describe, special_house

KINDS = ('signed', 'not signed', 'book club')
PROMPT = """Someone photographed bookshelves for a bookshop and wrote this note with the photos:

"{note}"

Below are the books read from each photo, in the order the photos were uploaded, shelf by shelf (shelf 1 is the top shelf)
and left to right along each shelf, each with an id in brackets. The readings can be partial or slightly wrong.

Which books does the note say are signed, not signed, book-club copies, or one publisher's (such as Easton Press or
Franklin Library)? A section ("signed up until the Asimov books")
covers every book from where it starts to where it ends. A landmark naming several books ("the Asimov books") is where those
books stand together; a lone one of them before that is inside the section, as an exception. Every book the note excepts
("which aren't signed") is "not signed", wherever it stands. Name only books the note covers, and none if it says nothing
about signatures, book clubs or publishers. When it is unclear which photo or shelf the note means, take the one its landmarks (titles,
authors, positions) fit.

Reply with JSON only: {"books": [{"id": "1.4", "is": "signed"}, {"id": "1.9", "is": "publisher", "publisher": "Easton Press"}],
"summary": "one sentence: the photo and shelf, and where each section starts and ends"}. "is" is one of: signed, not signed,
book club, publisher.

Books:
{books}"""


def listing(photos):
    """photos: [(k, name, books)]. Each photo's books shelf by shelf, left to right, as '[k.i] title, author'."""
    out = []
    for k, name, books in photos:
        out.append(f'Photo {k} ({name})')
        for s in sorted({b['shelf'] for b in books}):
            out.append(f'  Shelf {s}:')
            for i, b in sorted(((i, b) for i, b in enumerate(books) if b['shelf'] == s), key=lambda ib: (ib[1]['x0'], ib[1].get('y0', 0))):
                out.append(f"    [{k}.{i}] {clean(b.get('title')) or 'unread title'}, {clean(b.get('author')) or 'unread author'}")
    return '\n'.join(out)


def apply(claude, photos, budget):
    """photos: [(k, name, note, books)] for the photos that have a note. Sets book['from_note'] on the books each note covers.
    Returns {k: {'summary': ..., 'books': {kind: [label, ...]}}} for each photo with a note."""
    out = {}
    for note in dict.fromkeys((n or '').strip() for _, _, n, _ in photos):
        group = [(k, name, books) for k, name, n, books in photos if (n or '').strip() == note]
        text = claude.ask(SONNET, None, PROMPT.replace('{note}', note).replace('{books}', listing(group)), 4000, 4000, budget)
        m = re.search(r'\{.*\}', text, re.S)
        try: reply = json.loads(m.group(0)) if m else {}
        except ValueError: reply = {}
        by_k = {k: books for k, _, books in group}
        for k in by_k: out[k] = {'summary': str(reply.get('summary') or '').strip(), 'books': {}}
        for r in reply.get('books') or []:
            try:
                k, i = (int(x) for x in str(r.get('id')).split('.'))
                b, kind = by_k[k][i], str(r.get('is') or '').lower()
            except (ValueError, KeyError, IndexError, TypeError, AttributeError): continue
            if kind == 'publisher':
                house = special_house(str(r.get('publisher') or ''))
                if not house: continue
                if (b.get('publisher_hint') or {}).get('from') != 'the shelf setting':      # the setting is the user's own
                    b['publisher_hint'] = {'publisher': house, 'from': "the scan's note"}
                out[k]['books'].setdefault(house, []).append(describe(b))
                continue
            if kind not in KINDS: continue
            b['from_note'] = kind
            out[k]['books'].setdefault(kind, []).append(describe(b))
    return out
