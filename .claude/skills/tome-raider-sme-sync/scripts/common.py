"""Shared by the sync scripts: the config, a local copy of the app's stored JSON (runs, readings, marks), a copy of the app's
code at one commit with the shop's price files linked in, and a replay of that code's rules on every marked capture.

Everything lives under HOME (~/repos/personal-projects/norris/sme-page), outside the public repo: the price files and the
store copy are shop data. The storage key is read from its key file and never printed."""
import copy, datetime, json, os, shutil, subprocess, sys, tempfile

HOME = os.path.expanduser(os.environ.get('SME_PAGE_HOME', '~/repos/personal-projects/norris/sme-page'))


def path(*p):
    return os.path.join(HOME, *p)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def load_json(p, default=None):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except FileNotFoundError:
        if default is not None: return default
        raise


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f: json.dump(obj, f, indent=1, ensure_ascii=False, default=str)
    os.replace(p + '.tmp', p)


def load_config():
    cfg = load_json(path('config.json'))
    for k in ('poc', 'storage_secret_file'): cfg[k] = os.path.expanduser(cfg[k])
    return cfg


def git(repo, *args):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, check=True).stdout.strip()


def _connection_string(cfg):
    with open(cfg['storage_secret_file'], encoding='utf-8') as f:
        for line in f:
            if line.startswith('AZURE_STORAGE_CONNECTION_STRING='): return line.split('=', 1)[1].strip()
    raise SystemExit('no AZURE_STORAGE_CONNECTION_STRING line in ' + cfg['storage_secret_file'])


def mirror(cfg):
    """Copies the app's JSON objects under captures/ into .cache/store, only those that changed. Photos aren't copied."""
    from azure.storage.blob import ContainerClient
    c = ContainerClient.from_connection_string(_connection_string(cfg), cfg.get('container', 'shelfscan'))
    root, n = path('.cache', 'store'), 0
    for b in c.list_blobs(name_starts_with='captures/'):
        if not b.name.endswith('.json'): continue
        p, t = os.path.join(root, b.name), b.last_modified.timestamp()
        if os.path.exists(p) and os.path.getsize(p) == b.size and abs(os.path.getmtime(p) - t) < 1: continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + '.tmp', 'wb') as f: f.write(c.download_blob(b.name).readall())
        os.replace(p + '.tmp', p)
        os.utime(p, (t, t))
        n += 1
    return n


def code_at(cfg, commit='HEAD'):
    """A copy of the app's code at commit, with the gitignored price files linked in and var/store pointing at the store
    copy. Returns (dir, sha). Older copies are removed."""
    poc = cfg['poc']
    sha = git(poc, 'rev-parse', commit)
    d = path('.cache', 'code-' + sha[:12])
    if not os.path.isdir(os.path.join(d, 'shelfscan')):
        tmp = d + '.tmp'
        shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
        archive = subprocess.run(['git', '-C', poc, 'archive', sha], capture_output=True, check=True).stdout
        subprocess.run(['tar', '-x', '-C', tmp], input=archive, check=True)
        shutil.rmtree(d, ignore_errors=True); os.replace(tmp, d)
    for f in os.listdir(os.path.join(poc, 'data')):
        dst = os.path.join(d, 'data', f)
        if not os.path.lexists(dst): os.symlink(os.path.join(poc, 'data', f), dst)
    os.makedirs(os.path.join(d, 'var'), exist_ok=True)
    if not os.path.islink(os.path.join(d, 'var', 'store')): os.symlink(path('.cache', 'store'), os.path.join(d, 'var', 'store'))
    for old in os.listdir(path('.cache')):
        if old.startswith('code-') and old != os.path.basename(d) and not old.endswith('.tmp'):
            shutil.rmtree(path('.cache', old), ignore_errors=True)
    return d, sha


def import_code(d):
    """Imports the app's modules from code dir d, reading the store copy (never the live store)."""
    os.environ.pop('AZURE_STORAGE_CONNECTION_STRING', None)
    os.environ.setdefault('SHELFSCAN_HOME', tempfile.mkdtemp())
    sys.path.insert(0, d)
    from shelfscan import evaluate, marks, refdata, repo, rules
    from shelfscan.store import open_store
    return evaluate, marks, refdata, repo, rules, open_store(os.path.join(d, 'var', 'store'))


def replay(d, dated):
    """Re-applies the rules of code dir d to every marked capture's latest finished run and scores them against the marks.
    dated: {"<capture>/<photo index>": [{shelf, x0, printed}]}, printing years from the one-off dating test for runs made
    before runs dated their own candidates. Returns one row per answered mark."""
    evaluate, M, refdata, repo, rules, store = import_code(d)
    ref = refdata.Reference(price_files=[os.path.join(d, 'data', n) for n in refdata.DEFAULT_PRICE_FILES])
    rows = []
    for cid in M.capture_ids(store):
        mk = M.load(store, cid)[0]
        if not any(m.get('verdict') for m in mk.values()): continue
        run = repo.latest_run(store, cid, done_only=True)
        res = repo.load_results(store, cid, run['id']) if run else None
        if not res or not all(ph.get('readings') for ph in res['photos']): continue
        r = copy.deepcopy(res)
        for i, ph in enumerate(r['photos']):
            cats = {s['shelf']: s for s in ph.get('shelves') or []}
            cat_of = lambda s, cats=cats, ph=ph: (cats.get(s) or {}).get('category') or ph.get('category')
            mins = {s['category']: s.get('minimum') for s in cats.values()}
            books = copy.deepcopy(ph['readings'])
            when = {(x['shelf'], x['x0']): x['printed'] for x in dated.get(f'{cid}/{i}', [])}
            for b in books:
                if 'printed' not in b and (b['shelf'], b['x0']) in when: b['printed'] = when[(b['shelf'], b['x0'])]
            ph['pulls'], ph['leads'], ph['not_pulled'], ph['skipped'] = rules.evaluate(
                books, cat_of, ref, lambda c, mins=mins: mins.get(c) or ref.rules['minimum_value'][c])
            ph['readings'] = books
        s = evaluate.score(r, mk)
        by_key = {b['key']: b for b in M.books(r)}
        for o in s['outcomes']:
            item = (by_key.get(o['key']) or {}).get('item') or {}
            b, a = item.get('book') or {}, item.get('assessment') or {}
            m = mk.get(o['id']) or {}
            rows.append(dict(
                id=f"{cid}/{o['id']}", capture=cid, run=run['id'], label=o['label'], verdict=o['verdict'], note=o['note'] or '',
                marked_at=m.get('at'), outcome=o['outcome'], status=o['status'], value=a.get('value'), basis=a.get('basis'),
                reason=a.get('reason'), signals=a.get('signals') or [], checks=a.get('checks') or [],
                comps=[dict(price=c.get('price'), year=c.get('year'), file=c.get('file'), title=(c.get('title') or '')[:140])
                       for c in (a.get('comps') or [])[:6]],
                reading={k: b.get(k) for k in ('title', 'author', 'imprint', 'format', 'look', 'marks', 'spine_text', 'printed', 'confidence')},
                foot=b.get('foot'), notes=b.get('notes')))
    return rows


def score(rows):
    s = {'agree': 0, 'disagree': 0, 'not found': 0}
    for r in rows: s[r['outcome']] = s.get(r['outcome'], 0) + 1
    s['total'] = len(rows)
    return s


def money(v):
    return '' if v is None else f'${round(v):,}'
