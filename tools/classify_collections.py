"""Rebuilds the list of story collections, anthologies, omnibuses, poetry and essay collections in the price file, for the
Collections whitelist page: Sonnet reads every sale listing's title (about $1.20 for 11,000 listings; saved responses make a
re-run free), and the works are saved to the store as reference/collections.json. Nothing is written to the repository.

usage: python tools/classify_collections.py [--cap 1.50] [--local DIR]"""
import argparse, collections, concurrent.futures as cf, datetime, json, os, re, statistics, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from shelfscan.anthologies import CATALOG_KEY
from shelfscan.claude import SONNET, Budget, Claude
from shelfscan.refdata import Reference
from shelfscan.store import LocalStore, open_store

PROMPT = """Below are eBay sold-listing titles for books, numbered. Find every listing whose book is a collection of several
works in one volume:
- story: a collection of one author's short stories or novellas (e.g. The Illustrated Man, I Robot, Nine Stories, Dubliners)
- anthology: works by several authors in one volume
- omnibus: several novels by one author in one volume (e.g. The Bachman Books, a Library of America volume)
- poetry: a poetry collection
- essays: an essay or nonfiction-piece collection
Skip single novels, single long poems and single nonfiction books. Skip sets and lots of separate volumes.
Reply with JSON lines only, one per collection found, nothing else:
{"i": <number>, "kind": "story|anthology|omnibus|poetry|essays", "work": "<the book's title>", "author": "<author or editor>"}

Listings:
"""
CHUNK = 400


def classify(rows, claude, budget):
    def one(idx):
        text = claude.ask(SONNET, None, PROMPT + '\n'.join(f'{j + 1}. {rows[k]["title"]}' for j, k in enumerate(idx)), 16000, 6000, budget)
        out = []
        for ln in text.splitlines():
            try: r = json.loads(ln.strip().strip(','))
            except ValueError: continue
            try: out.append(dict(r, row=idx[int(r['i']) - 1]))
            except (KeyError, ValueError, IndexError, TypeError): continue
        return out
    chunks = [list(range(i, min(i + CHUNK, len(rows)))) for i in range(0, len(rows), CHUNK)]
    with cf.ThreadPoolExecutor(4) as ex: return [f for res in ex.map(one, chunks) for f in res]


def works(rows, found):
    def norm(s): return re.sub(r'\s+', ' ', re.sub(r"[^a-z0-9 ]", ' ', (s or '').lower().replace('&', 'and'))).strip()
    groups = collections.defaultdict(list)
    for f in found:
        a = norm(f.get('author')).split()
        groups[(re.sub(r'^(the|a|an) ', '', norm(f['work'])), a[-1] if a else '')].append(f)
    out = []
    for fs in groups.values():
        prices = [float(rows[f['row']]['price']) for f in fs]
        top = lambda k: collections.Counter(f.get(k) or '' for f in fs).most_common(1)[0][0]
        out.append(dict(work=top('work'), author=top('author'), kind=top('kind'), sales=len(prices), median=statistics.median(prices),
                        max=max(prices), example=rows[fs[0]['row']]['title']))
    return sorted(out, key=lambda w: -w['median'])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cap', type=float, default=1.50)
    ap.add_argument('--local')
    ap.add_argument('--cache', default=os.path.join(os.environ.get('SHELFSCAN_HOME') or 'var', 'cache'))
    a = ap.parse_args()
    rows, budget = Reference().prices, Budget(a.cap)
    ws = works(rows, classify(rows, Claude(a.cache), budget))
    (LocalStore(a.local) if a.local else open_store()).put_json(CATALOG_KEY, {
        'made': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'source': 'price file listings classified by Claude Sonnet 5.5', 'works': ws})
    print(len(ws), 'works saved;', collections.Counter(w['kind'] for w in ws), f'${budget.spent:.2f}')
