"""Bibliographic notes for the candidates: the first edition, the points that identify it, and the lookalikes worth less.

One text-only Opus call per photo (about 9 cents for 15 books, 40 seconds), from the model's own knowledge, so the notes say
how sure they are. Opus identified obscure books better than Sonnet and invented less. Web search helped further on local
and small-press titles but cost about 10 cents a book, so it is left out for now."""
import json, re
from .claude import OPUS
from .rules import clean

NOTES_PROMPT = """You are briefing an experienced antiquarian bookseller who is deciding which books on a shop shelf to take down
and examine. The books below were read from their spines in a photo, so titles and authors may be partial or slightly wrong.
Give the bibliographic facts an expert wants at hand, specific to each book. Write only what is particular to the book:
no generic advice ("check the copyright page", "verify the signature", "condition matters"), nothing an expert already
knows, and nothing the marks below already say.

For each book:
- first: the first edition: year, publisher and city, for the UK and the US where they differ
  (e.g. "1932, Chatto & Windus, London; US: Doubleday, Doran, 1932").
- points: how to tell the first printing or the valuable issue: issue points, binding or jacket details, the first
  jacket price, number line or printing statement, limitation, the illustrator's first appearance, and any famous flag
  or error. Mention a limited issue if one exists, but not signatures: the app adds its own signature reminder from sales.
- watch: lookalikes worth much less, if any: book-club editions, later printings, facsimiles, reprints.
- form: novel, story collection, anthology, omnibus, poetry, essays, nonfiction or other. A story collection is one
  author's short stories or novellas in one volume; an anthology has several authors; an omnibus is several novels in one.
- author: the author's full name as you identify the book (the editor or translator for an anthology or a translation),
  even when the spine gave only a surname, a publisher or nothing. Empty if you can't identify the book.
- publisher: this copy's publisher or series as the spine reading shows it (Modern Library, Everyman's Library and Folio
  Society count), else empty. Not the first edition's publisher unless the spine shows it.
- about: if the book is about a person (a biography, criticism, a study or a bibliography), that person's full name;
  else empty. Mark Schorer's "Sinclair Lewis" is about Sinclair Lewis.
- confidence: high, medium or low, for how sure you are of these facts.
If you can't identify the book from what was read, say so in first and leave points and watch empty. Never invent a point
or a publisher: an empty field is better than a wrong one. Keep each field under 40 words.

Reply with JSON only: {"books": [{"n": 1, "first": "...", "points": "...", "watch": "...", "form": "novel", "author": "...",
"publisher": "", "about": "", "confidence": "high"}]}

Books:
"""
MAX_BOOKS = 40


def describe(n, item):
    b = item['book']
    bits = [f"title: {clean(b.get('title')) or 'unread'}", f"author: {clean(b.get('author')) or 'unread'}"]
    if b.get('spine_text'): bits.append(f'spine reads: "{b["spine_text"]}"')
    imprint = clean(b.get('imprint')) or clean((b.get('foot') or {}).get('imprint'))
    if imprint: bits.append(f'publisher on spine: {imprint}')
    if b.get('look'): bits.append({'antique': 'looks pre-1925', 'old': 'looks 1925-1970', 'modern': 'looks modern'}.get(b['look'], b['look']))
    if b.get('format') and b['format'] != 'unclear': bits.append(b['format'])
    if b.get('jacket') == 'yes': bits.append('in a dust jacket')
    if b.get('marks'): bits.append(f"marks: {b['marks']}")
    return f'{n}. ' + '; '.join(bits)


def add_notes(claude, items, budget, model=OPUS):
    """Writes item['assessment']['notes'] = {first, points, watch, form, author, publisher, about, confidence} for each item it
    can, also kept on the book so the rules can use it (who wrote it, whose book it's about, the copy's publisher). Returns how many."""
    todo = [x for x in items if clean(x['book'].get('title')) and 'notes' not in x['assessment'] and 'notes' not in x['book']][:MAX_BOOKS]
    if not todo: return 0
    for x in todo: x['book']['notes_tried'] = True        # from here the price-file list of collections may decide alone
    prompt = NOTES_PROMPT + '\n'.join(describe(i, x) for i, x in enumerate(todo, 1))
    text = claude.ask(model, None, prompt, 20000, 12000, budget)       # Opus thinks first, so leave room
    m = re.search(r'\{.*\}', text, re.S)
    try: books = json.loads(m.group(0))['books'] if m else []
    except ValueError: books = []
    done = 0
    for r in books:
        try: x = todo[int(r['n']) - 1]
        except (KeyError, ValueError, IndexError, TypeError): continue
        note = {k: str(r.get(k) or '').strip() for k in ('first', 'points', 'watch', 'form', 'author', 'publisher', 'about', 'confidence')}
        note['confidence'] = note['confidence'].lower()
        if note['form']: x['book']['form'] = note['form'].lower()
        if note['first'] or note['points'] or note['author'] or note['about']:
            x['assessment']['notes'] = x['book']['notes'] = note
            done += 1
    return done
