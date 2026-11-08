"""Scores every answered mark against the rules of one version of the app's code, on each marked capture's latest run:
writes replay.json in the run folder (the score, and per mark its outcome, status, value, reading, signals and the sales used).
Free: no Claude calls, reads the local store copy only. Run pull.py first.
usage: python replay.py RUN_DIR [--commit SHA]   (default: the poc repo's HEAD, which should be what's deployed)"""
import argparse, os
from common import code_at, load_config, load_json, now_iso, path, replay, save_json, score

ap = argparse.ArgumentParser()
ap.add_argument('run_dir'); ap.add_argument('--commit', default='HEAD')
a = ap.parse_args()
cfg = load_config()
d, sha = code_at(cfg, a.commit)
rows = replay(d, load_json(path(cfg.get('dated', 'dated.json')), {}))
s = score(rows)
save_json(os.path.join(a.run_dir, 'replay.json'), dict(made=now_iso(), code=sha, score=s, rows=rows))
print(f"code {sha[:12]}: {s['agree']} of {s['total']} marks agree, {s['disagree']} disagree, {s['not found']} not found")
