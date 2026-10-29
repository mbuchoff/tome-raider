"""Checks each photo's part of a run's budget: its own spending, the shared cap, Claude calls through a part, the pipeline
giving every photo its part, and a run's cap of $2 a photo. No Claude calls: a fake client stands in.
usage: python tests/budget_check.py"""
import contextlib, json, os, sys, tempfile, time, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shelfscan import claude as cl, images, notes, pipeline, reader, repo, rules
from shelfscan.store import LocalStore


def check(name, got, want=True):
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, '' if ok else f'got {got!r}, want {want!r}')
    if not ok: check.failed = True
check.failed = False


def rec(c): return {'cost': c, 'usage': {'input_tokens': 10, 'output_tokens': 5}}


# parts: their own spending, one cap
b = cl.Budget(1.0); p1, p2 = b.part(), b.part()
p1.settle(0, rec(0.3)); p2.settle(0, rec(0.25)); p2.settle(0, rec(0.05)); p2.hit()
s = b.summary()
check('the job totals every part', (s['spent'], s['calls'], s['cached_calls']), (0.6, 3, 1))
check('each part keeps its own', s['photos'], [{'spent': 0.3, 'calls': 1, 'cached_calls': 0}, {'spent': 0.3, 'calls': 2, 'cached_calls': 1}])
p1.reserve(0.3)
try: p2.reserve(0.2); check('the cap is shared: a reservation past it is refused', False)
except cl.CapReached: check('the cap is shared: a reservation past it is refused', True)
p1.release(0.3); p2.reserve(0.2)
check('released, the room is back', round(b.reserved, 4), 0.2)
check('a budget without parts has no photos', 'photos' in cl.Budget(1.0).summary(), False)


# a Claude call through a part: paid once, then from the cache
class FakeStream:
    def __init__(self, fail): self.fail = fail
    def __enter__(self):
        if self.fail: raise RuntimeError('overloaded')
        return self
    def __exit__(self, *a): return False
    def get_final_message(self):
        return types.SimpleNamespace(usage=types.SimpleNamespace(input_tokens=1000, output_tokens=500), stop_reason='end_turn',
                                     content=[types.SimpleNamespace(type='text', text='ok')])
fail = []
fake = types.SimpleNamespace(messages=types.SimpleNamespace(count_tokens=lambda **kw: types.SimpleNamespace(input_tokens=1000),
                                                             stream=lambda **kw: FakeStream(bool(fail and fail.pop()))))
c = cl.Claude(tempfile.mkdtemp(), api_key='unused'); c.client = fake
b = cl.Budget(2.0); p1, p2 = b.part(), b.part()
check('a paid call answers', c.ask(cl.SONNET, None, 'read this', 1000, 1000, p1), 'ok')
check('and is charged to its photo', b.summary()['photos'][0], {'spent': round(cl.cost(cl.SONNET, {'input_tokens': 1000, 'output_tokens': 500}), 4), 'calls': 1, 'cached_calls': 0})
c.ask(cl.SONNET, None, 'read this', 1000, 1000, p2)
check('the same call again comes from the cache, counted on its photo', b.summary()['photos'][1], {'spent': 0.0, 'calls': 0, 'cached_calls': 1})
b2 = cl.Budget(2.0, fresh_since=time.time()); p3 = b2.part()          # a run reading its photos again, queued just now
c.ask(cl.SONNET, None, 'read this', 1000, 1000, p3)
check('a run reading again pays for a response saved before it was queued', (b2.summary()['calls'], b2.summary()['cached_calls']), (1, 0))
c.ask(cl.SONNET, None, 'read this', 1000, 1000, p3)
check('but reuses one saved since, as after a restart', (b2.summary()['calls'], b2.summary()['cached_calls']), (1, 1))
fail.append(True)
try: c.ask(cl.SONNET, None, 'something new', 1000, 1000, p2)
except RuntimeError: pass
check('a failed call gives its reservation back', (round(b.reserved, 6), b.summary()['photos'][1]['calls']), (0.0, 0))


# the pipeline gives every photo its own part, and a photo stopped at the cap keeps what it spent
COST = {'a.jpg': 0.12, 'b.jpg': 0.34}
def fake_read(claude, im, budget, wants_second, progress=None, note=None):
    budget.settle(0, rec(COST[im]))
    if im == 'b.jpg' and stop: raise cl.CapReached('stopped before a call that could take this job past its cap')
    return [{'shelf': 1}], []
pipeline._ref = types.SimpleNamespace(version=lambda: 'test', rules={'minimum_value': {'cheap': 50}}, collections=None)
images.load, images.annotate = (lambda path: os.path.basename(path)), (lambda im, boxes: b'jpeg')
reader.read_photo, reader.check_feet, notes.add_notes = fake_read, (lambda *a: False), (lambda *a: None)
rules.evaluate = lambda *a: ([], [], [], [])
for stop in (False, True):
    d = tempfile.mkdtemp()
    pipeline.save_job(d, dict(id='x', created='2026-10-27T00:00:00+00:00', status='queued', cap=4.0,
                              photos=[dict(file=f, name=f, category='cheap') for f in COST]))
    job, results = pipeline.run(d, claude=None)
    check(f'{"stopped" if stop else "done"}: each photo\'s cost in the results', [x['spent'] for x in results['cost']['photos']], [0.12, 0.34])
    check(f'{"stopped" if stop else "done"}: and on the job, for run.json', job['cost']['photos'] == results['cost']['photos'] and job['status'], 'stopped' if stop else 'done')
check('a stopped photo has no results, but its cost is kept', (len(results['photos']), results['cost']['spent']), (1, 0.46))


# a run's cap is $2 a photo
store = LocalStore(tempfile.mkdtemp())
cap2 = repo.create_capture(store, [('a.jpg', b'a'), ('b.jpg', b'b')], dict(category='cheap', overrides={}, minimum=None, note='', second_read=False))
cap1 = repo.create_capture(store, [('c.jpg', b'c')], dict(category='cheap', overrides={}, minimum=None, note='', second_read=False))
check('two photos: a $4 cap', repo.start_run(store, cap2['id'], 2.0)['cap'], 4.0)
check('one photo: $2', repo.start_run(store, cap1['id'], 2.0)['cap'], 2.0)
sys.exit(1 if check.failed else 0)
