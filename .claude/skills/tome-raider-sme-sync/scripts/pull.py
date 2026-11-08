"""Starts a sync: refreshes the local copy of the app's stored JSON, makes a run folder (runs/<UTC time>), and writes
app.json there: every capture with its latest run and its marks, plus the code version the newest run used.
Prints the run folder. Read-only on the app; no Claude calls.
usage: python pull.py"""
import datetime, glob, os
from common import HOME, git, load_config, load_json, mirror, now_iso, path, save_json

cfg = load_config()
n = mirror(cfg)
run_dir = path('runs', datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
os.makedirs(os.path.join(run_dir, 'db'), exist_ok=True)

captures, newest = [], None
for cdir in sorted(glob.glob(path('.cache', 'store', 'captures', '*'))):
    if not os.path.isdir(cdir): continue
    cap = load_json(os.path.join(cdir, 'capture.json'), {})
    runs = [load_json(p, {}) for p in sorted(glob.glob(os.path.join(cdir, 'runs', '*', 'run.json')))]
    done = [r for r in runs if r.get('status') == 'done']
    last = done[-1] if done else None
    marks = load_json(os.path.join(cdir, 'marks.json'), {})
    marks = marks.get('marks', marks) if isinstance(marks, dict) else {}
    captures.append(dict(
        id=os.path.basename(cdir), created=cap.get('created'), photos=len(cap.get('photos') or []),
        latest_run=last and dict(id=last.get('id'), created=last.get('created'), pulls=last.get('pulls'), leads=last.get('leads'),
                                 spent=(last.get('cost') or {}).get('spent'), baseline=((last.get('baseline') or {}).get('sha') or '')[:12]),
        marks=[dict(id=k, verdict=m.get('verdict'), label=m.get('label'), note=m.get('note') or '', at=m.get('at'))
               for k, m in marks.items() if m.get('verdict') or m.get('note')]))
    if last and (newest is None or (last.get('created') or '') > (newest.get('created') or '')): newest = last

head = git(cfg['poc'], 'rev-parse', 'HEAD')
dirty = bool(git(cfg['poc'], 'status', '--porcelain', '--untracked-files=no'))
live = ((newest or {}).get('baseline') or {}).get('sha') or ''
save_json(os.path.join(run_dir, 'app.json'), dict(pulled=now_iso(), objects_refreshed=n, poc_head=head, poc_dirty=dirty,
                                                  newest_run_baseline=live, captures=captures))
print(run_dir)
print(f'{n} stored objects refreshed; {len(captures)} captures; {sum(len(c["marks"]) for c in captures)} marks')
print(f'poc HEAD {head[:12]}{" (uncommitted changes)" if dirty else ""}; newest run used {live[:12] or "unknown"}'
      + ('' if not live or live == head else '  <- differs: is HEAD deployed?'))
