"""Checks the renderer's guards on made-up data (no shop data, no network): placeholders, the disagreement coverage rules,
the generated answer boxes. Run after changing render.py.
usage: python selftest.py"""
import re, sys
from render import Problem, render

failed = False
def check(name, got, want=True):
    global failed
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, '' if ok else f'got {got!r}, want {want!r}')
    failed |= not ok

def row(label, outcome, value=None, capture='cap1', status='pull'):
    return dict(id=f'{capture}/{label}', capture=capture, label=label, outcome=outcome, value=value, status=status)

shell = '<main>\n__MAIN__\n</main><script>const DATA = __DATA__; const QUESTIONS = __QUESTIONS__;</script>'
def page(entries, items=(), update='<p>{{agree}} of {{total}}, was {{prev_agree}}.</p>'):
    return dict(intro=dict(eyebrow='e', heading='h', html='<p>i</p>'), update=dict(date='1 November', heading='u', html=update),
                items=list(items), disagreements=dict(id='disagreements', heading='The {{ndisagree}} books', groups=[dict(heading='g', entries=entries)], notes=''),
                general=dict(id='general', heading='Anything else?', html='', notes=''))
rows = [row('Harbor Nights, Hollis Prentiss', 'disagree', 120.0), row('Low Tide, Hollis Prentiss', 'agree', 80.0),
        row('Low Tide, Hollis Prentiss', 'agree', 95.0, capture='cap2'), row('The Tin Lantern, Orrin Blake', 'disagree', None, status='skipped')]
both = [dict(books=['Harbor Nights'], html='<i>Harbor Nights</i> at {{value:Harbor Nights}}'), dict(books=['Tin Lantern'], html='<i>The Tin Lantern</i>')]

out = render(page(both), rows, {'agree': 1}, {}, shell)
check('scores and values come from the replay', ('2 of 4, was 1.' in out, 'The 2 books' in out, 'at $120' in out), (True, True, True))
def problem(**kw):
    try: render(**kw); return ''
    except Problem as e: return str(e)
base = dict(rows=rows, prev={}, data={}, shell=shell)
check('a disagreement left off the page is refused', 'Tin Lantern' in problem(page=page(both[:1]), **base))
check('an entry that no longer disagrees is refused', 'no longer disagree' in problem(page=page(both + [dict(books=['Low Tide'], html='x')]), **base))
check('a value matching books with different values is refused', 'different values' in problem(page=page(both, update='{{value:Low Tide}}'), **base))
check('...unless a capture picks one', '$95' in render(page(both, update='{{value:Low Tide@cap2}}'), **base))
check('a book with no value is refused', 'has no value' in problem(page=page(both, update='{{value:Tin Lantern}}'), **base))
check('an unknown placeholder is refused', 'unknown placeholder' in problem(page=page(both, update='{{agre}}'), **base))
call = dict(id='call-x', kind='call', topic='T', title='Q?', html='<p>b</p>', legend='L', options=[dict(value='a', label='A', why='w')], notes='ph')
done = dict(id='done-y', kind='done', topic='D', title='Did it', done_on='2026-11-01', changed='<p>c</p>')
out = render(page(both, items=[call, done]), **base)
check('a call gets its options, notes box and a place in QUESTIONS',
      ('name="call-x"' in out, 'id="note-call-x"' in out, re.search(r'QUESTIONS = (\[.*?\])', out).group(1)), (True, True, '["call-x", "disagreements", "general"]'))
check('a done item gets its pill, eyebrow and tick box', ('pill done' in out, 'Done 1 November · D' in out, 'data-ack="done-y"' in out), (True, True, True))
check('one call: "One decision"', 'One decision<' in out)
check('duplicate ids are refused', 'duplicate' in problem(page=page(both, items=[call, call]), **base))
sys.exit(1 if failed else 0)
