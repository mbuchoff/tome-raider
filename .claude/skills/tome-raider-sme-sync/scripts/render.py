"""Builds the page (page.html) from page.json, the run's replay.json, data.json and shell.html, all under sme-page/.

Numbers come from the replay, not from prose: in any html field, {{agree}} {{disagree}} {{notfound}} {{total}}
{{prev_agree}} {{prev_total}} {{ndisagree}} {{ncalls}} are the scores (prev_ from the last sync), and {{value:words}} is the
value of the one marked book whose label contains those words ({{value:words@capture}} to pick a capture). The list of
disagreements is checked against the replay: every disagreeing mark must be covered by an entry, and every entry must
still match one. Exits non-zero, writing nothing, on any problem.
usage: python render.py RUN_DIR"""
import html as H, json, os, re, sys
from common import load_json, money, path, score

MONTHS = 'January February March April May June July August September October November December'.split()


class Problem(Exception): pass


def nice_date(iso):
    y, m, d = (int(x) for x in iso[:10].split('-'))
    return f'{d} {MONTHS[m - 1]}'


def matches(rows, words):
    words, cap = (words.split('@', 1) + [None])[:2]
    w = words.strip().lower()
    return [r for r in rows if w in r['label'].lower() and (not cap or r['capture'].startswith(cap.strip()))]


def fill(text, ctx, rows, where):
    def one(m):
        k = m.group(1).strip()
        if k.startswith('value:'):
            hit = matches(rows, k[6:])
            vals = {r['value'] for r in hit}
            if not hit: raise Problem(f'{where}: {{{{{k}}}}} matches no marked book')
            if len(vals) > 1: raise Problem(f'{where}: {{{{{k}}}}} matches books with different values: ' + '; '.join(f"{r['label']} @{r['capture']} {money(r['value'])}" for r in hit))
            v = vals.pop()
            if v is None: raise Problem(f'{where}: {{{{{k}}}}} has no value (status {hit[0]["status"]})')
            return money(v)
        if k not in ctx: raise Problem(f'{where}: unknown placeholder {{{{{k}}}}}')
        return str(ctx[k])
    return re.sub(r'\{\{([^{}]+)\}\}', one, text or '')


def said(quotes):
    if not quotes: return ''
    return '<div class="said">' + ''.join(f'<p>“{H.escape(q["quote"])}” <span>{q["source"]}</span></p>' for q in quotes) + '</div>'


def notes_box(it, default_label='Notes'):
    i, ph = it['id'], H.escape(it.get('notes') or '', quote=True)
    return (f'<label class="note" for="note-{i}">{it.get("notes_label", default_label)} <span>(optional)</span>\n'
            f'      <textarea id="note-{i}" placeholder="{ph}"></textarea></label>\n'
            f'    <div class="status" id="status-{i}" aria-live="polite"></div>')


def options(it):
    i, opts = it['id'], it.get('options') or []
    if not opts: return ''
    inner = ''.join(f'<label class="option"><input type="radio" name="{i}" id="{i}-{o["value"]}" value="{o["value"]}">'
                    f'<span class="what">{o["label"]}</span>' + (f'<span class="why">{o["why"]}</span>' if o.get('why') else '') + '</label>\n'
                    for o in opts)
    if it.get('layout') == 'yesno': inner = f'<div class="yesno">{inner}</div>'
    return f'<fieldset id="ask-{i}">\n      <legend>{it.get("legend", "Your answer")}</legend>\n      {inner}</fieldset>'


def section(it, eyebrow, ctx, rows):
    where = f'item {it["id"]}'
    body = said(it.get('said')) + fill(it.get('html'), ctx, rows, where)
    ack = it.get('ack', it['kind'] == 'done')
    if it.get('changed') is not None:
        pill = ('done', 'Done') if it['kind'] == 'done' else ('next', 'Next')
        inner = fill(it['changed'], ctx, rows, where) + (f'<div class="ack" data-ack="{it["id"]}"></div>' if ack else '')
        body += f'<div class="changed"><div class="head"><span class="pill {pill[0]}">{pill[1]}</span>What changed in Tome Raider</div>{inner}</div>'
    elif ack and f'data-ack="{it["id"]}"' not in body:
        body += f'<div class="ack" data-ack="{it["id"]}"></div>'
    tail = options(it) + ('\n    ' + notes_box(it) if it['id'] in ctx['_questions'] else '')
    return (f'  <section class="q" id="{it["id"]}">\n    <div class="eyebrow">{eyebrow}</div>\n    <h2>{it["title"]}</h2>\n'
            f'    {body}\n    {tail}\n  </section>\n')


def render(page, rows, prev, data, shell):
    items = [i for i in page['items'] if i.get('status', 'open') != 'removed']
    ids = [i['id'] for i in items] + [page['disagreements']['id'], page['general']['id']]
    if len(set(ids)) != len(ids): raise Problem('duplicate item ids: ' + ', '.join(sorted({i for i in ids if ids.count(i) > 1})))
    s = score(rows)
    dis = [r for r in rows if r['outcome'] == 'disagree']
    calls = [i for i in items if i['kind'] == 'call']
    questions = [i['id'] for i in items if i.get('options') or i.get('notes') is not None]
    questions += [x['id'] for x in (page['disagreements'], page['general']) if x.get('notes') is not None]
    ctx = dict(agree=s['agree'], disagree=s['disagree'], notfound=s['not found'], total=s['total'], ndisagree=len(dis),
               ncalls=len(calls), prev_agree=prev.get('agree', '?'), prev_total=prev.get('total', '?'), _questions=set(questions))

    out = []
    intro = page['intro']
    out.append(f'  <header class="intro">\n    <div class="eyebrow">{intro["eyebrow"]}</div>\n    <h1>{intro["heading"]}</h1>\n'
               f'    {fill(intro["html"], ctx, rows, "intro")}\n    <div class="banner" id="banner" hidden></div>\n  </header>\n')
    up = page['update']
    out.append(f'  <section class="update" id="update">\n    <div class="eyebrow">Updated {up["date"]}</div>\n    <h2>{up["heading"]}</h2>\n'
               f'    <div class="score">\n      <div><b>{s["agree"]}</b> <span>of the {s["total"]} books you’ve marked agree with you</span></div>\n'
               f'      <div><b>{s["disagree"]}</b> <span>don’t yet</span></div>\n    </div>\n'
               f'    {fill(up["html"], ctx, rows, "update")}\n    <p class="ack-count" id="ack-count" aria-live="polite"></p>\n  </section>\n')
    if calls:
        n = len(calls)
        word = 'One Two Three Four Five Six Seven Eight Nine Ten'.split()[n - 1] if n <= 10 else str(n)
        out.append(f'  <div class="part"><div class="eyebrow">Your call</div><h2>{word} decision{"s" if n != 1 else ""}</h2>\n'
                   f'    {fill(page.get("calls_intro", ""), ctx, rows, "calls_intro")}</div>\n')
        for n, it in enumerate(calls, 1): out.append(section(it, f'Your call {n} of {len(calls)} · {it["topic"]}', ctx, rows))
    qs = [i for i in items if i['kind'] == 'question']
    if qs:
        out.append('  <div class="part"><div class="eyebrow">Questions</div><h2>About your books</h2></div>\n')
        for it in qs: out.append(section(it, f'Question · {it["topic"]}', ctx, rows))
    done = [i for i in items if i['kind'] == 'done']
    if done:
        out.append('  <div class="part"><div class="eyebrow">Done</div><h2>Just finished</h2></div>\n')
        for it in done: out.append(section(it, f'Done {nice_date(it["done_on"])} · {it["topic"]}', ctx, rows))
    unknown = [i['id'] for i in items if i['kind'] not in ('call', 'question', 'done')]
    if unknown: raise Problem('items of unknown kind: ' + ', '.join(unknown))

    # where we still disagree: every disagreeing mark covered, every entry still current
    dg = page['disagreements']
    covered, lines = {}, []
    for g in dg['groups']:
        lis = []
        for e in g['entries']:
            pool = rows if e.get('any') else dis
            hit = [r for w in e['books'] for r in matches(pool, w)]
            stale = [w for w in e['books'] if not matches(pool, w)]
            if stale: raise Problem(f'disagreements: {stale} no longer {"match a marked book" if e.get("any") else "disagree"} (entry "{e["books"]}")')
            if not e.get('any'):
                for r in hit: covered.setdefault(r['id'], []).append(e['books'])
            lis.append(f'      <li>{fill(e["html"], ctx, rows, "disagreements")}</li>\n')
        lines.append(f'    <h3>{g["heading"]}</h3>\n    <ul class="small">\n{"".join(lis)}    </ul>\n')
    missing = [r for r in dis if r['id'] not in covered]
    if missing: raise Problem('disagreements not on the page: ' + '; '.join(f"{r['label']} ({r['capture']}, {r['status']} {money(r['value'])})" for r in missing))
    out.append(f'  <section class="q" id="{dg["id"]}">\n    <div class="eyebrow">Where we still disagree</div>\n'
               f'    <h2>{fill(dg["heading"], ctx, rows, "disagreements")}</h2>\n    {fill(dg.get("intro", ""), ctx, rows, "disagreements")}\n'
               + ''.join(lines) + f'    {notes_box(dg)}\n  </section>\n')
    for a in page.get('asides', []): out.append(f'  <p class="aside">{fill(a["html"], ctx, rows, "aside")}</p>\n')
    gen = page['general']
    out.append(f'  <section class="end">\n    <h2>{gen["heading"]}</h2>\n    {fill(gen.get("html", ""), ctx, rows, "general")}\n    {notes_box(gen)}\n  </section>\n')

    main = '\n'.join(out)
    for k in ('__MAIN__', '__DATA__', '__QUESTIONS__'):
        if shell.count(k) != 1: raise Problem(f'shell.html must hold {k} once')
    return shell.replace('__MAIN__', main).replace('__DATA__', json.dumps(data, ensure_ascii=False)).replace('__QUESTIONS__', json.dumps(questions))


if __name__ == '__main__':
    run_dir = sys.argv[1]
    rep = load_json(os.path.join(run_dir, 'replay.json'))
    state = load_json(path('state.json'), {})
    try:
        page = render(load_json(path('page.json')), rep['rows'], state.get('score') or {}, load_json(path('data.json'), {}),
                      open(path('shell.html'), encoding='utf-8').read())
    except Problem as e:
        sys.exit(f'not rendered: {e}')
    with open(path('page.html'), 'w', encoding='utf-8') as f: f.write(page)
    s = rep['score']
    print(f"page.html written ({len(page):,} bytes): {s['agree']} of {s['total']} agree, {s['disagree']} disagree")
