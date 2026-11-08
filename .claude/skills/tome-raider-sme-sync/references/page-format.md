# page.json: the page as data

`render.py` turns this file into `page.html`. It draws the parts that must stay consistent itself: the score, the answer boxes and tick boxes, the "Your call N of M" numbering and the list of answer ids the page's script saves. Everything else is HTML you write in the fields below, in the page's existing CSS classes.

## Top level

```json
{
  "title": "Tome Raider Questions",
  "intro": {"eyebrow": "Tome Raider · questions for the bookshop", "heading": "What’s still open", "html": "<p>…</p>"},
  "update": {"date": "2 November", "heading": "Thank you for checking our work", "html": "<p class=\"muted small\">…</p>"},
  "calls_intro": "<p>…</p>",
  "items": [ … ],
  "disagreements": {"id": "disagreements", "heading": "The {{ndisagree}} books Tome Raider still gets wrong, and why",
                    "intro": "<p>…</p>", "groups": [ … ], "notes": "placeholder for its notes box"},
  "asides": [{"html": "…"}],
  "general": {"id": "general", "heading": "Anything else?", "html": "…", "notes": "placeholder"}
}
```

- **update:** the score block ("N of the M books you've marked agree with you") is generated above `html`. In `html`, say what changed since he last looked, give a "Corrected since you last saw this page:" list when something earlier was wrong, and ask him to tick the boxes. Keep it short.
- **intro:** what the page holds right now. Update it when the set of items changes.

## Items

| kind | what it is | lifecycle |
|---|---|---|
| `call` | a decision only he can make, with options | open, then answered (his `answers/<id>.choice`), then the work is done, then it becomes a `done` item |
| `question` | something we need him to tell us about specific books | open, then he writes a note, then it's acted on, then removed or turned into a `done` item |
| `done` | a change we made, for him to confirm | waiting for his tick (`acks/<id>.done`), then removed to archive.json |

Fields:
- `id`: the key his answers and ticks are saved under. Never reuse an id for something else; his old answer would show up in it.
- `kind`, `topic` (the eyebrow's subject), `title` (the h2), `added` (YYYY-MM-DD).
- `done_on`: done items only; the eyebrow says "Done 2 November · topic".
- `said`: optional `[{"quote": "his exact words", "source": "Your note on <i>Low Tide</i>"}]`, shown first.
- `html`: the body.
- `changed`: optional HTML, wrapped in the "What changed in Tome Raider" box with a Done pill (done items) or Next pill (others). A done item's tick box goes at its end.
- `options`: calls only. `[{"value": "rare", "label": "On rare shelves", "why": "what happens, with the cost"}]`. Add `legend` (the question above the options) and optionally `"layout": "yesno"`.
- `notes`: a placeholder string. It adds a notes box, and the id joins the saved answers. A call always gets one, even with an empty placeholder. Optional `notes_label` (default "Notes").
- `ack`: done items default to `true` (a tick box). Use `false` for a done item that only asks questions, then give it `notes`.
- `keep`: set it to `true`, with the reason in the item's text, to keep something check.py would otherwise flag: a ticked item you still need, or an answered call whose work isn't live yet.
- `status`: `"removed"` hides an item. Prefer `page.py remove`, which archives it with his answers.

Order on the page: update, then calls ("Your call"), then questions, then done items ("Just finished"), then disagreements, asides and "Anything else?".

## Placeholders (any html field)

- `{{agree}} {{disagree}} {{notfound}} {{total}} {{ndisagree}} {{ncalls}}`: from this run's replay.
- `{{prev_agree}} {{prev_total}}`: from the last sync.
- `{{value:Harbor Nights}}`: the value of the one marked book whose label contains "Harbor Nights", e.g. `$120`. Labels are the reading's "Title, Author" as stored with his mark, e.g. "Tin Lantern, Blake" with no leading "The". Use `{{value:Low Tide@cap2}}` (a capture id prefix) when two marked copies differ. The renderer refuses a placeholder that matches nothing, matches copies with different values, or has no value.

Write "about {{value:…}}" when the page rounds. Don't type a book's price by hand. check.py warns when a typed `$N` next to an italic title disagrees with the replay.

## Disagreements

```json
{"heading": "Your calls above", "entries": [
  {"books": ["Harbor Nights", "The Tin Lantern"], "html": "<b>1, first editions:</b> <i>Harbor Nights</i> ({{value:Harbor Nights}}, …)"}
]}
```

- `books` are phrases matched case-insensitively inside the labels of disagreeing marks. An author's name covers every volume of a set by them.
- Every disagreeing mark must be covered, and every phrase must still match one, or render.py refuses. That's how the list stays exactly the current disagreements.
- An entry with `"any": true` matches any marked book, agreeing or not, for notes like "Right call, wrong book".

Useful groups, in this order:
- **"Your calls above":** what one of his open decisions would settle.
- **"Your notes above":** what we need him to tell us.
- **"Rules your marks split on":** where his own marks disagree with each other, so we leave the rule alone.
- **"Ours to fix":** only if the user agreed to fix it. Otherwise "We're looking into it".
- **"The rest":** anything else.

## Writing for him

He's a bookseller, not a programmer. Use plain words and short sentences:
- Say "listed to check", "pulled", "valued from N sales", "his files" (his sales spreadsheets).
- Avoid "lead", "comps", "regex", "replay", "rule engine".
- Name books in italics.
- Quote his notes exactly, with their source.
- Say what a change did on *his* shelves, with an example or two, rather than describing the mechanism.
- When we got something wrong earlier, say so plainly in the update's corrections list.

## Snippets

```html
<div class="said"><p>“his words” <span>Your note on <i>Title</i></span></p></div>
<p class="proposal"><b>Proposed change.</b> …</p>
<div class="evidence"><h3>What it would cost</h3><ul class="small"><li>…</li></ul></div>
<h4>Two it now gets wrong</h4><ul><li>…</li></ul>
<p class="muted small">…</p>
```

The examples of shelf copies with their sales (`<div class="ex" data-ex="KEY">`, `<a data-run="KEY">`) come from `data.json` (`fixes`). Add one only with data from the store, never typed by hand.
