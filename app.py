"""Tome Raider (proof of concept): a small web app that takes shelf photos and returns the books worth pulling.

A capture is the photos and settings as uploaded; each run is one pass of the pipeline over a capture, recording the code
version (baseline) it ran on, so an old capture can be run again after the rules change (see DESIGN.md).

Settings (environment variables):
  ANTHROPIC_API_KEY      required
  ANTHROPIC_ADMIN_KEY    optional; an Admin API key, read-only use: the account's spending, for the "credit left" figure
  SHELFSCAN_PASSCODE     required; the one passcode that signs people in
  SHELFSCAN_SECRET       signs the session cookie; set it so sign-ins survive restarts
  SHELFSCAN_HOME         the app's own disk: saved responses, the spend ledger, the credit balance, working folders and any
                         old jobs to migrate (default ./var; /home/shelfscan on Azure)
  AZURE_STORAGE_CONNECTION_STRING, SHELFSCAN_CONTAINER   where captures, runs and marks are kept (else ./var/store)
  SHELFSCAN_JOB_CAP      Claude spending cap per run in $ (default 2.00)
  SHELFSCAN_DAILY_CAP    Claude spending cap per UTC day in $ (default 10.00)
  SHELFSCAN_PRICE_FILES  comma-separated price files, if not the ones in data/"""
import datetime, hmac, mimetypes, os, queue, secrets, shutil, threading, time
from flask import Flask, Response, abort, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename
from shelfscan import anthologies_web, credit, marks, marks_web, pipeline, repo, version
from shelfscan import index as capture_index
from shelfscan.claude import Claude
from shelfscan.rules import CATEGORIES
from shelfscan.store import NotFound, open_store

HOME = os.environ.get('SHELFSCAN_HOME') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'var')
JOBS, CACHE, WORK = os.path.join(HOME, 'jobs'), os.path.join(HOME, 'cache'), os.path.join(HOME, 'work')
JOB_CAP = float(os.environ.get('SHELFSCAN_JOB_CAP', '2.00'))
DAILY_CAP = float(os.environ.get('SHELFSCAN_DAILY_CAP', '10.00'))
ADMIN_KEY = os.environ.get('ANTHROPIC_ADMIN_KEY', '').strip()
DESCRIPTIONS = {'thrift': 'under $10 a book', 'cheap': '$10–$30 a book', 'rare': '$30–$100 a book', 'extreme': '$100 and up', 'signed': 'signed copies'}
os.makedirs(HOME, exist_ok=True)
store = open_store(os.path.join(HOME, 'store'))

app = Flask(__name__)
app.secret_key = os.environ.get('SHELFSCAN_SECRET') or secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=150 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('SHELFSCAN_INSECURE_COOKIE') != '1', PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30))

work = queue.Queue()                    # (capture id, run id)
failures, failures_lock = {}, threading.Lock()


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


app.jinja_env.globals.update(ordinal=ordinal, categories=CATEGORIES, commit_time=version.commit_time, is_current_baseline=version.is_current)


def queue_run(cid):
    """Starts a run of the capture on the current baseline and returns its id; if the capture already has a run on this
    baseline with the same settings (not failed or stopped), returns that one instead of running it twice."""
    same = repo.matching_run(store, cid, version.baseline())
    if same: return same['id']
    run = repo.start_run(store, cid, JOB_CAP)
    work.put((cid, run['id']))
    return run['id']


app.config['START_RUN'] = queue_run
app.config['STORE'] = store
app.register_blueprint(marks_web.bp)
app.register_blueprint(anthologies_web.bp)


def recent_captures(n=25):
    """The newest captures' summaries, from the index (rebuilt from the store if it's missing)."""
    entries = capture_index.read(store).get('captures') or []
    if not entries and repo.capture_ids(store):
        capture_index.rebuild(store)
        entries = capture_index.read(store).get('captures') or []
    return entries[:n]


def spent_today():
    midnight = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return credit.ledger_spent(HOME, midnight)


def worker():
    claude = Claude(CACHE, ledger=os.path.join(HOME, 'spend.jsonl'))
    while True:
        cid, rid = work.get()
        try: pipeline.run_stored(store, cid, rid, claude, WORK)
        except Exception as e:                 # keep the worker alive whatever happens to one run
            print('run', cid, rid, 'failed:', e, flush=True)


def boot():
    """Copies old job folders into the store (once), requeues interrupted runs, then works the queue. Runs in the background,
    so the app answers at once while a first migration uploads the old scans."""
    if os.path.isdir(JOBS):
        try:
            from tools import migrate_jobs
            migrate_jobs.migrate(JOBS, store, log=lambda m: print(m, flush=True))
        except (ImportError, NotImplementedError) as e: print('old jobs not migrated:', e, flush=True)
    pending = []                                       # resume work interrupted by a restart; saved responses aren't paid twice
    for cid in repo.capture_ids(store):
        for run in repo.list_runs(store, cid):
            if run['status'] in ('queued', 'running'): pending.append((run['created'], cid, run['id']))
    for _, cid, rid in sorted(pending): work.put((cid, rid))
    worker()


def start():
    shutil.rmtree(WORK, ignore_errors=True)            # working folders left by a restart; their runs start again in boot()
    threading.Thread(target=boot, daemon=True).start()


def capture_or_404(cid):
    if not repo.valid_id(cid): abort(404)
    try: return repo.load_capture(store, cid)
    except NotFound: abort(404)


def stream(data, name, max_age=86400):
    return Response(data, mimetype=mimetypes.guess_type(name)[0] or 'application/octet-stream',
                    headers={'Cache-Control': f'private, max-age={max_age}'})


@app.before_request
def require_sign_in():
    if request.endpoint in ('login', 'health', 'static'): return None
    if not session.get('ok'): return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
        with failures_lock:
            recent = [t for t in failures.get(ip, []) if t > time.time() - 600]
            failures[ip] = recent
        passcode = os.environ.get('SHELFSCAN_PASSCODE', '')
        if len(recent) >= 10: error = 'Too many attempts. Try again in a few minutes.'
        elif passcode and hmac.compare_digest(request.form.get('passcode', '').encode(), passcode.encode()):
            session.permanent, session['ok'] = True, True
            return redirect(url_for('index'))
        else:
            with failures_lock: failures.setdefault(ip, []).append(time.time())
            error = 'That passcode is not right.'
    return render_template('login.html', error=error)


@app.route('/healthz')
def health():
    return 'ok'


@app.route('/')
def index(credit_error=None, error=None):
    return render_template('index.html', captures=recent_captures(), descriptions=DESCRIPTIONS, job_cap=JOB_CAP, daily_cap=DAILY_CAP,
                           spent_today=spent_today(), credit=credit.remaining(HOME, ADMIN_KEY), credit_error=credit_error, error=error)



@app.route('/credit', methods=['POST'])
def set_credit():
    """Records the Anthropic balance shown in the Console; spending from then on is subtracted from it."""
    try:
        balance = float(request.form.get('balance', ''))
        if not 0 <= balance <= 100000: raise ValueError
    except ValueError:
        return index('Enter the balance in dollars, for example 37.50.'), 400
    try: credit.set_balance(HOME, ADMIN_KEY, balance, note=request.form.get('note', ''))
    except Exception as e: return index(f"The balance wasn't saved, because the account's spending couldn't be read: {e}"), 502
    return redirect(url_for('index'))


@app.route('/captures', methods=['POST'])
def create_capture():
    if spent_today() >= DAILY_CAP: return index(error=f"Today's ${DAILY_CAP:.2f} limit is used up."), 429
    files = [f for f in request.files.getlist('photos') if f and f.filename][:10]
    if not files: return redirect(url_for('index'))
    category = request.form.get('category', 'cheap')
    if category not in CATEGORIES: abort(400)
    try: minimum = max(1.0, float(request.form['minimum'])) if request.form.get('minimum') else None
    except ValueError: minimum = None
    settings = dict(category=category, overrides={str(k): v for k, v in pipeline.parse_overrides(request.form.get('overrides')).items()},
                    minimum=minimum, note=request.form.get('note', '')[:300], second_read=request.form.get('second_read') == '1')
    photos = [(secure_filename(f.filename), f.read()) for f in files]
    cid = repo.find_capture(store, repo.fingerprint([data for _, data in photos]))
    if cid: repo.update_settings(store, cid, settings)          # the same photos again: a new run of that capture
    else: cid = repo.create_capture(store, photos, settings)['id']
    queue_run(cid)
    return redirect(url_for('capture_page', cid=cid))         # the page shows the newest run


@app.route('/captures/<cid>')
def capture_page(cid):
    to = repo.valid_id(cid) and not store.exists(repo.capture_key(cid)) and repo.resolve_alias(store, cid)
    if to: return redirect(url_for('capture_page', cid=to, **request.args), 301)       # merged into another capture
    capture = capture_or_404(cid)
    runs = repo.list_runs(store, cid)
    rid = request.args.get('run')
    run = next((r for r in runs if r['id'] == rid), None) if rid else (runs[0] if runs else None)
    if rid and not run: abort(404)
    results = repo.load_results(store, cid, run['id']) if run and run['status'] not in ('queued', 'running') else None
    busy = any(r['status'] in ('queued', 'running') for r in runs)
    same = repo.matching_run(store, cid, version.baseline(), capture)
    return render_template('capture.html', capture=capture, runs=runs, run=run, results=results, busy=busy, same_run=same,
                           baseline=version.baseline(), new_run_url=url_for('new_run', cid=cid),
                           file_url=lambda n: url_for('run_file', cid=cid, rid=run['id'], name=n),
                           results_url=run and url_for('run_results', cid=cid, rid=run['id']),
                           marks=marks.for_run(store, cid, results) if results else {}, marks_url=f'/captures/{cid}/marks',
                           credit=credit.remaining(HOME, ADMIN_KEY))


@app.route('/captures/<cid>/runs', methods=['POST'])
def new_run(cid):
    capture_or_404(cid)
    if spent_today() >= DAILY_CAP: abort(429)
    return redirect(url_for('capture_page', cid=cid, run=queue_run(cid)))


@app.route('/captures/<cid>/runs/<rid>/files/<name>')
def run_file(cid, rid, name):
    if not (repo.valid_id(cid) and repo.valid_id(rid)): abort(404)
    name = secure_filename(name)
    try: return stream(repo.get_run_file(store, cid, rid, name), name)
    except NotFound: abort(404)


@app.route('/captures/<cid>/runs/<rid>/results.json')
def run_results(cid, rid):
    if not (repo.valid_id(cid) and repo.valid_id(rid)): abort(404)
    try: return stream(store.get_bytes(repo.results_key(cid, rid)), 'results.json', max_age=0)
    except NotFound: abort(404)


@app.route('/captures/<cid>/photos/<name>')
def capture_photo(cid, name):
    if not repo.valid_id(cid): abort(404)
    name = secure_filename(name)
    try: return stream(repo.get_photo(store, cid, name), name)
    except NotFound: abort(404)


@app.route('/jobs/<cid>')
@app.route('/jobs/<cid>/<path:rest>')
def old_job(cid, rest=None):
    """Links from before captures: a job became a capture with the same id."""
    return redirect(url_for('capture_page', cid=cid), 301)


start()
