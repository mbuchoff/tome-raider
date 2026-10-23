"""Checks the "credit left" arithmetic against a fake usage report, with no network calls.
usage: python tests/credit_check.py"""
import datetime, json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shelfscan import credit

U = datetime.timezone.utc
KEY = 'sk-ant-admin01-test'
T0 = datetime.datetime(2026, 10, 22, 15, 30, 20, tzinfo=U)
clock = [T0.timestamp()]
events = []                    # (time, model, input tokens, output tokens)
calls = []


def fake(url, params, headers, timeout):
    """The usage report as observed: hourly buckets for finished hours only, minute buckets up to now; two buckets a page."""
    calls.append((params['bucket_width'], params['starting_at'][5:16], params['ending_at'][5:16], params.get('page')))
    assert headers['x-api-key'] == KEY and headers['anthropic-version'] == '2023-06-01' and params['group_by[]'] == 'model'
    step = {'1m': credit.MINUTE, '1h': credit.HOUR}[params['bucket_width']]
    now = datetime.datetime.fromtimestamp(clock[0], U)
    t, end = (datetime.datetime.fromisoformat(params[k].replace('Z', '+00:00')) for k in ('starting_at', 'ending_at'))
    buckets = []
    while t < end and (t + step <= now if step == credit.HOUR else t <= now):
        rows = {}
        for at, model, n_in, n_out in events:
            if t <= at < t + step:
                r = rows.setdefault(model, {'model': model, 'uncached_input_tokens': 0, 'output_tokens': 0, 'cache_read_input_tokens': 0,
                                            'cache_creation': {'ephemeral_5m_input_tokens': 0, 'ephemeral_1h_input_tokens': 0}})
                r['uncached_input_tokens'] += n_in; r['output_tokens'] += n_out
        buckets.append({'starting_at': params['starting_at'], 'results': list(rows.values())})
        t += step
    i = int(params.get('page') or 0)
    return 200, {'data': buckets[i:i + 2], 'has_more': i + 2 < len(buckets), 'next_page': str(i + 2) if i + 2 < len(buckets) else None}


def check(name, got, want):
    ok = got == want
    print(('ok  ' if ok else 'FAIL'), name, got, '' if ok else f'(wanted {want})')
    if not ok: check.failed = True


def ledger(at, cost):
    """A paid call by this app, as the Claude client records it."""
    with open(os.path.join(home, 'spend.jsonl'), 'a') as f: f.write(json.dumps({'at': at.timestamp(), 'cost': cost}) + '\n')


check.failed = False
home = tempfile.mkdtemp()
now = lambda: clock[0]
credit.LAG = datetime.timedelta(0)             # first, a usage report that is never behind

check('no key', credit.remaining(home, '', fake, now)['error'], 'no Admin API key is set')
check('no balance', credit.remaining(home, KEY, fake, now)['error'], 'no balance entered yet')

events.append((T0 - datetime.timedelta(minutes=5), 'claude-sonnet-5-5', 10**6, 0))          # $2 before the balance is entered: not subtracted
state = credit.set_balance(home, KEY, '50', fake, now)
check('balance saved', state, {'balance': 50.0, 'set_at': '2026-10-22T15:30:20+00:00'})
check('key checked with one minute', calls[-1], ('1m', '10-22T15:30', '10-22T15:31', None))
check('nothing spent since', (credit.remaining(home, KEY, fake, now)['amount'], credit.remaining(home, KEY, fake, now)['error']), (50.0, None))

# Sonnet $2 + $10 per million, Opus $4 + $20: $0.30 this hour, $1.40 overnight, $0.05 in the current minute
events += [(T0 + datetime.timedelta(minutes=10), 'claude-sonnet-5-5', 100000, 10000),
           (T0 + datetime.timedelta(hours=3), 'claude-opus-5-5', 100000, 50000),
           (T0 + datetime.timedelta(hours=9, minutes=0, seconds=30), 'claude-sonnet-5-5', 0, 5000)]
ledger(T0 + datetime.timedelta(hours=9, minutes=0, seconds=30), 0.05)        # the current minute comes from the ledger
clock[0] = (T0 + datetime.timedelta(seconds=60)).timestamp()
n = len(calls)
check('cached for 120 s', (credit.remaining(home, KEY, fake, now)['amount'], len(calls) - n), (50.0, 0))
clock[0] = (T0 + datetime.timedelta(hours=9, seconds=40)).timestamp()            # 00:31 the next day
r = credit.remaining(home, KEY, fake, now)
check('spent since, to the minute', (r['amount'], r['error']), (round(50 - 0.30 - 1.40 - 0.05, 2), None))
widths = [(c[0], c[1], c[2]) for c in calls[n:] if c[3] is None]
check('minutes, hours, minutes', widths, [('1m', '10-22T15:30', '10-22T16:00'), ('1h', '10-22T16:00', '10-23T00:00'), ('1m', '10-23T00:00', '10-23T00:31')])
check('pages followed', len(calls) - n > len(widths), True)
check('as of', r['as_of'], '2026-10-23T00:31:00+00:00')

# a model without a price is named, not silently left out
events.append((T0 + datetime.timedelta(hours=9, seconds=45), 'claude-mystery-1', 1000, 1000))
clock[0] += 130
r = credit.remaining(home, KEY, fake, now)
check('unpriced model named', (r['amount'], r['error']), (48.25, "usage of claude-mystery-1 isn't counted, because the app has no price for it"))

# a failed fetch keeps the last good figure and says why
clock[0] += 130
r = credit.remaining(home, KEY, lambda *a: (401, {'error': {'message': 'invalid x-api-key'}}), now)
check('error keeps last amount', (r['amount'], r['error']), (48.25, 'Usage API 401: invalid x-api-key'))

# entering a new balance resets the cache at once, and only later spending counts
credit.set_balance(home, KEY, 60, fake, now)
check('new balance', credit.remaining(home, KEY, fake, now)['amount'], 60.0)

# a usage report an hour behind: within the hour only the app's own calls count; another use of the account shows later
credit.LAG = datetime.timedelta(minutes=60)
set_at = datetime.datetime.fromtimestamp(clock[0], U)
events.append((set_at + datetime.timedelta(minutes=5), 'claude-sonnet-5-5', 0, 100000))           # $1, a scan by the app
ledger(set_at + datetime.timedelta(minutes=5), 1.0)
events.append((set_at + datetime.timedelta(minutes=6), 'claude-opus-5-5', 0, 100000))             # $2, someone else's script
clock[0] = (set_at + datetime.timedelta(minutes=10)).timestamp()
n = len(calls)
check('within the hour: the app only', (credit.remaining(home, KEY, fake, now)['amount'], len(calls) - n), (59.0, 0))
clock[0] = (set_at + datetime.timedelta(minutes=80)).timestamp()
check('an hour on: the whole account', credit.remaining(home, KEY, fake, now)['amount'], 57.0)
sys.exit(1 if check.failed else 0)
