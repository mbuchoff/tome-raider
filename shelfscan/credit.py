"""The Anthropic credit left on the whole account: a balance typed in from the Console, minus everything the account has
spent since. Needs an Admin API key.

Spending comes from the usage report, priced with the app's price table. On settled days that matches the Cost API to the
cent, where the Cost API leaves out the current day and lags by hours. Hourly buckets cover finished hours only, so the
partial hours at each end are added up by the minute. The usage report itself ran 20 minutes and more behind when tested
(not the 5 minutes the docs give), so it is read only up to LAG ago, and this app's own spending since then comes from its
ledger of paid calls: the app's scans show at once, other use of the account after LAG.
There is no API for the balance itself, and none for purchases, so the balance is typed in again after adding credits."""
import datetime, json, os, threading, time, urllib.error, urllib.parse, urllib.request
from .claude import PRICE

URL = 'https://api.anthropic.com/v1/organizations/usage_report/messages'
HOUR, MINUTE = datetime.timedelta(hours=1), datetime.timedelta(minutes=1)
LIMIT = {'1m': 1440, '1h': 168}        # the most buckets a page can hold
CACHE_SECONDS = 120                    # a refresh is up to three requests; the API allows polling about once a minute
LAG = datetime.timedelta(minutes=60)   # how far behind the usage report is trusted to be complete
_cache, _lock = {}, threading.Lock()


def _get(url, params, headers, timeout):
    """GET -> (status, parsed JSON or None)."""
    req = urllib.request.Request(url + '?' + urllib.parse.urlencode(params), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try: return e.code, json.load(e)
        except ValueError: return e.code, None


def _iso(t):
    return t.strftime('%Y-%m-%dT%H:%M:%SZ')


def _minute(t):
    return t.replace(second=0, microsecond=0)


def _dollars(r, unpriced):
    """One usage row at standard prices. Prompt-cache reads cost 0.1x input, writes 1.25x (5 minutes) or 2x (1 hour)."""
    pin, pout = PRICE.get(r.get('model'), (None, None))
    cc = r.get('cache_creation') or {}
    tokens = [r.get('uncached_input_tokens') or 0, r.get('output_tokens') or 0, r.get('cache_read_input_tokens') or 0,
              cc.get('ephemeral_5m_input_tokens') or 0, cc.get('ephemeral_1h_input_tokens') or 0]
    if pin is None:
        if any(tokens): unpriced.add(r.get('model') or 'an unnamed model')
        return 0.0
    return sum(n * p for n, p in zip(tokens, (pin, pout, pin * 0.1, pin * 1.25, pin * 2))) / 1e6


def _usage(admin_key, start, end, width, unpriced, get):
    if end <= start: return 0.0
    params = {'starting_at': _iso(start), 'ending_at': _iso(end), 'bucket_width': width, 'group_by[]': 'model', 'limit': LIMIT[width]}
    total = 0.0
    while True:
        status, body = get(URL, params, {'anthropic-version': '2023-06-01', 'x-api-key': admin_key}, 10)
        if status != 200:
            raise RuntimeError(f"Usage API {status}: {((body or {}).get('error') or {}).get('message', 'no detail')}")
        total += sum(_dollars(r, unpriced) for b in body.get('data') or [] for r in b.get('results') or [])
        if not body.get('has_more'): return total
        params['page'] = body['next_page']


def account_spent(admin_key, since, now, get=_get):
    """(dollars the whole account spent from since to now, models it couldn't price): minutes up to the first whole hour,
    whole hours, then minutes again up to now."""
    unpriced, since, now = set(), _minute(since), _minute(now) + MINUTE
    first = since if since.minute == 0 else since.replace(minute=0) + HOUR
    last = now.replace(minute=0)
    if first > last: return _usage(admin_key, since, now, '1m', unpriced, get), unpriced
    spent = sum(_usage(admin_key, a, b, w, unpriced, get) for a, b, w in ((since, first, '1m'), (first, last, '1h'), (last, now, '1m')))
    return spent, unpriced


def _path(home):
    return os.path.join(home, 'credit.json')


def ledger_spent(home, since):
    """Dollars this app's own paid calls cost from since on, from the ledger the Claude client writes."""
    t, total = since.timestamp(), 0.0
    try:
        for ln in open(os.path.join(home, 'spend.jsonl')):
            try: r = json.loads(ln)
            except ValueError: continue
            if r['at'] >= t: total += r['cost']
    except OSError: pass
    return total


def set_balance(home, admin_key, balance, get=_get, clock=time.time):
    """Records the Console balance now; spending from this minute on is subtracted. Reads the last minute once to check the key."""
    if not admin_key: raise RuntimeError('no Admin API key is set')
    now = datetime.datetime.fromtimestamp(clock(), datetime.timezone.utc)
    account_spent(admin_key, now, now, get)
    state = {'balance': round(float(balance), 2), 'set_at': now.isoformat(timespec='seconds')}
    tmp = _path(home) + f'.{os.getpid()}.{threading.get_ident()}.tmp'
    json.dump(state, open(tmp, 'w'), indent=1)
    os.replace(tmp, _path(home))
    with _lock: _cache.pop(home, None)
    return state


def remaining(home, admin_key, get=_get, clock=time.time):
    """dict(amount, as_of, set_at, error). amount is None, with the reason in error, until there's a key, a balance and one
    good fetch; after a failed fetch the last good amount stays, with the error alongside."""
    try: state = json.load(open(_path(home)))
    except (OSError, ValueError): state = None
    if not admin_key: return dict(amount=None, as_of=None, set_at=None, error='no Admin API key is set')
    if not state: return dict(amount=None, as_of=None, set_at=None, error='no balance entered yet')
    with _lock:                         # one fetch at a time; other page loads wait for it and share the result
        c = _cache.get(home)
        if c and c['state'] == state and clock() - c['at'] < CACHE_SECONDS: return c['result']
        now = datetime.datetime.fromtimestamp(clock(), datetime.timezone.utc)
        try:
            since = datetime.datetime.fromisoformat(state['set_at'])
            cutoff = max(since, _minute(now - LAG))         # the usage report up to here; the app's ledger after it
            spent, unpriced = account_spent(admin_key, since, cutoff - MINUTE, get) if cutoff > since else (0.0, set())
            spent += ledger_spent(home, cutoff)
            result = dict(amount=round(state['balance'] - spent, 2), as_of=now.isoformat(timespec='seconds'), set_at=state['set_at'],
                          error=f"usage of {', '.join(sorted(unpriced))} isn't counted, because the app has no price for it" if unpriced else None)
        except Exception as e:
            last = c['result'] if c and c['state'] == state else {}
            result = dict(amount=last.get('amount'), as_of=last.get('as_of'), set_at=state['set_at'], error=str(e)[:200])
        _cache[home] = dict(state=state, at=clock(), result=result)
        return result
