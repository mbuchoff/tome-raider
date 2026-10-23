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
  or error. Mention a signed or limited issue if one exists.
- watch: lookalikes worth much less, if any: book-club editions, later printings, facsimiles, reprints.
- confidence: high, medium or low, for how sure you are of these facts.
If you can't identify the book from what was read, say so in first and leave points and watch empty. Never invent a point
or a publisher: an empty field is better than a wrong one. Keep each field under 40 words.

Reply with JSON only: {"books": [{"n": 1, "first": "...", "points": "...", "watch": "...", "confidence": "high"}]}

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
    """Writes item['assessment']['notes'] = {first, points, watch, confidence} for each item it can. Returns how many."""
    todo = [x for x in items if clean(x['book'].get('title')) and 'notes' not in x['assessment']][:MAX_BOOKS]
    if not todo: return 0
    prompt = NOTES_PROMPT + '\n'.join(describe(i, x) for i, x in enumerate(todo, 1))
    text = claude.ask(model, None, prompt, 20000, 12000, budget)       # Opus thinks first, so leave room
    m = re.search(r'\{.*\}', text, re.S)
    try: books = json.loads(m.group(0))['books'] if m else []
    except ValueError: books = []
    done = 0
    for r in books:
        try: x = todo[int(r['n']) - 1]
        except (KeyError, ValueError, IndexError, TypeError): continue
        note = {k: str(r.get(k) or '').strip() for k in ('first', 'points', 'watch', 'confidence')}
        if note['first'] or note['points']:
            x['assessment']['notes'] = note
            done += 1
    return done
