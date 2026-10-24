"""The baseline a run used: the code's git commit, from version.json (written by deploy.sh) or from git in a checkout."""
import functools, json, os, subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@functools.lru_cache(maxsize=1)
def baseline():
    """{'sha', 'committed_at', 'dirty'}, or None when neither version.json nor git can say."""
    try:
        v = json.load(open(os.path.join(HERE, 'version.json')))
        if v.get('sha'): return {'sha': v['sha'], 'committed_at': v.get('committed_at'), 'dirty': bool(v.get('dirty'))}
    except (OSError, ValueError): pass
    try:
        git = lambda *a: subprocess.run(['git', '-C', HERE, *a], capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        return {'sha': git('rev-parse', 'HEAD'), 'committed_at': git('log', '-1', '--format=%cI'), 'dirty': bool(git('status', '--porcelain'))}
    except (OSError, subprocess.SubprocessError): return None


def commit_time(b):
    """The baseline's commit time in UTC as 'YYYY-MM-DD HH:MM', the way the pages show times, or None before git."""
    import datetime
    if not b or not b.get('sha'): return None
    try: t = datetime.datetime.fromisoformat(b['committed_at']).astimezone(datetime.timezone.utc)
    except (KeyError, TypeError, ValueError): return b['sha'][:7]
    return t.strftime('%Y-%m-%d %H:%M')
