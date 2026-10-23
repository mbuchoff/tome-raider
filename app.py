"""Shelf Scanner proof of concept: a small web app that takes shelf photos and returns the books worth pulling.

Settings (environment variables):
  ANTHROPIC_API_KEY      required
  ANTHROPIC_ADMIN_KEY    optional; an Admin API key, read-only use: the account's spending, for the "credit left" figure
  SHELFSCAN_PASSCODE     required; the one passcode that signs people in
  SHELFSCAN_SECRET       signs the session cookie; set it so sign-ins survive restarts
  SHELFSCAN_HOME         where jobs and saved responses live (default ./var; /home/shelfscan on Azure)
  SHELFSCAN_JOB_CAP      Claude spending cap per scan in $ (default 2.00)
  SHELFSCAN_DAILY_CAP    Claude spending cap per UTC day in $ (default 10.00)
  SHELFSCAN_PRICE_FILES  comma-separated price files, if not the ones in data/"""
import datetime, hmac, json, os, queue, secrets, shutil, threading, time, uuid
from flask import Flask, abort, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename
from shelfscan import credit, pipeline
from shelfscan.claude import Claude
from shelfscan.rules import CATEGORIES

HOME = os.environ.get('SHELFSCAN_HOME') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'var')
JOBS, CACHE = os.path.join(HOME, 'jobs'), os.path.join(HOME, 'cache')
JOB_CAP = float(os.environ.get('SHELFSCAN_JOB_CAP', '2.00'))
DAILY_CAP = float(os.environ.get('SHELFSCAN_DAILY_CAP', '10.00'))
ADMIN_KEY = os.environ.get('ANTHROPIC_ADMIN_KEY', '').strip()
KEEP_DAYS = 30
DESCRIPTIONS = {'thrift': 'under $10 a book', 'cheap': '$10–$30 a book', 'rare': '$30–$100 a book', 'extreme': '$100 and up', 'signed': 'signed copies'}
os.makedirs(JOBS, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('SHELFSCAN_SECRET') or secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=150 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('SHELFSCAN_INSECURE_COOKIE') != '1', PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30))

work = queue.Queue()
failures, failures_lock = {}, threading.Lock()
feedback_lock = threading.Lock()
VERDICTS = ('worth', 'not', 'should')        # worth pulling, not worth pulling, should have been pulled


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


app.jinja_env.globals.update(ordinal=ordinal, categories=CATEGORIES)


def job_dir(job_id):
    if not job_id.isalnum(): abort(404)
    d = os.path.join(JOBS, job_id)
    if not os.path.isdir(d): abort(404)
    return d


def load_feedback(d):
    try: return json.load(open(os.path.join(d, 'feedback.json')))
    except (OSError, ValueError): return {}


def all_jobs():
    out = []
    for name in os.listdir(JOBS):
        try:
            j = pipeline.load_job(os.path.join(JOBS, name))
            j['marked'] = len(load_feedback(os.path.join(JOBS, name)))
            out.append(j)
        except (OSError, ValueError): pass
    return sorted(out, key=lambda j: j['created'], reverse=True)


def spent_today():
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    return sum((j.get('cost') or {}).get('spent', 0) for j in all_jobs() if (j.get('started') or j['created'])[:10] == today)


def worker():
    claude = Claude(CACHE, ledger=os.path.join(HOME, 'spend.jsonl'))
    while True:
        job_id = work.get()
        try:
            d = os.path.join(JOBS, job_id)
            job, results = pipeline.run(d, claude)
            job['pulls'] = sum(len(p['pulls']) for p in results['photos'])
            job['leads'] = sum(len(p.get('leads', [])) for p in results['photos'])
            pipeline.save_job(d, job)
        except Exception as e:                 # keep the worker alive whatever happens to one job
            print('job', job_id, 'failed:', e, flush=True)


def start():
    cutoff = time.time() - KEEP_DAYS * 86400
    for name in os.listdir(JOBS):
        d = os.path.join(JOBS, name)
        if os.path.getmtime(d) < cutoff: shutil.rmtree(d, ignore_errors=True)
    for j in sorted(all_jobs(), key=lambda j: j['created']):          # resume work interrupted by a restart; saved responses aren't paid twice
        if j['status'] in ('queued', 'running'): work.put(j['id'])
    threading.Thread(target=worker, daemon=True).start()


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
def index(credit_error=None):
    return render_template('index.html', jobs=all_jobs()[:25], descriptions=DESCRIPTIONS, job_cap=JOB_CAP, daily_cap=DAILY_CAP, spent_today=spent_today(),
                           credit=credit.remaining(HOME, ADMIN_KEY), credit_error=credit_error)


@app.route('/credit', methods=['POST'])
def set_credit():
    """Records the Anthropic balance shown in the Console; spending from then on is subtracted from it."""
    try:
        balance = float(request.form.get('balance', ''))
        if not 0 <= balance <= 100000: raise ValueError
    except ValueError:
        return index('Enter the balance in dollars, for example 37.50.'), 400
    try: credit.set_balance(HOME, ADMIN_KEY, balance)
    except Exception as e: return index(f"The balance wasn't saved, because the account's spending couldn't be read: {e}"), 502
    return redirect(url_for('index'))


@app.route('/jobs', methods=['POST'])
def create_job():
    if spent_today() >= DAILY_CAP:
        return render_template('job.html', job={'created': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'status': 'refused',
                               'error': f'Today\'s ${DAILY_CAP:.2f} limit is used up.'}, results=None, can_rerun=False), 429
    files = [f for f in request.files.getlist('photos') if f and f.filename]
    if not files: return redirect(url_for('index'))
    category = request.form.get('category', 'cheap')
    if category not in CATEGORIES: abort(400)
    try: minimum = max(1.0, float(request.form['minimum'])) if request.form.get('minimum') else None
    except ValueError: minimum = None
    overrides = {str(k): v for k, v in pipeline.parse_overrides(request.form.get('overrides')).items()}
    job_id = uuid.uuid4().hex[:12]
    d = os.path.join(JOBS, job_id)
    os.makedirs(d)
    photos = []
    for i, f in enumerate(files[:10], 1):
        ext = os.path.splitext(secure_filename(f.filename))[1].lower() or '.jpg'
        name = f'upload{i}{ext}'
        f.save(os.path.join(d, name))
        photos.append(dict(file=name, name=secure_filename(f.filename) or name, category=category, overrides=overrides,
                           minimum=minimum, note=request.form.get('note', '')[:300], second_read=request.form.get('second_read') == '1'))
    job = dict(id=job_id, created=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), status='queued',
               cap=JOB_CAP, photos=photos, progress='waiting to start')
    pipeline.save_job(d, job)
    work.put(job_id)
    return redirect(url_for('job_page', job_id=job_id))


@app.route('/jobs/<job_id>')
def job_page(job_id):
    d = job_dir(job_id)
    job = pipeline.load_job(d)
    rp = os.path.join(d, 'results.json')
    results = None
    if os.path.exists(rp) and job['status'] not in ('queued', 'running'):
        try: results = json.load(open(rp))
        except ValueError: job['error'] = job.get('error') or 'The results file is damaged; run the scan again (saved responses make it free).'
    return render_template('job.html', job=job, results=results, can_rerun=job['status'] not in ('queued', 'running'),
                           rerun_url=url_for('rerun', job_id=job_id), file_url=lambda n: url_for('job_file', job_id=job_id, name=n),
                           feedback=load_feedback(d), feedback_url=url_for('feedback', job_id=job_id), credit=credit.remaining(HOME, ADMIN_KEY),
                           results_url=url_for('job_file', job_id=job_id, name='results.json'))


@app.route('/jobs/<job_id>/feedback', methods=['POST'])
def feedback(job_id):
    """Records one verdict on a book: worth pulling, not worth it, or should have been pulled. An empty verdict clears it."""
    d = job_dir(job_id)
    data = request.get_json(silent=True) or {}
    key, verdict = str(data.get('key', ''))[:80], data.get('verdict') or ''
    if not key or (verdict and verdict not in VERDICTS): abort(400)
    with feedback_lock:
        fb = load_feedback(d)
        if verdict:
            fb[key] = {'verdict': verdict, 'label': str(data.get('label', ''))[:200], 'category': str(data.get('category', ''))[:20],
                       'value': data.get('value') or None, 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}
        else:
            fb.pop(key, None)
        tmp = os.path.join(d, f'feedback.json.{threading.get_ident()}.tmp')
        json.dump(fb, open(tmp, 'w'), indent=1); os.replace(tmp, os.path.join(d, 'feedback.json'))
    return {'ok': True, 'marked': len(fb)}


@app.route('/feedback.json')
def all_feedback():
    """Every verdict across all scans, for tuning the rules."""
    out = []
    for j in all_jobs():
        for key, v in load_feedback(os.path.join(JOBS, j['id'])).items():
            out.append(dict(job=j['id'], created=j['created'], key=key, **v))
    return {'feedback': out}


@app.route('/jobs/<job_id>/rerun', methods=['POST'])
def rerun(job_id):
    d = job_dir(job_id)
    job = pipeline.load_job(d)
    if job['status'] not in ('queued', 'running'):
        job.update(status='queued', progress='waiting to start', error=None)
        pipeline.save_job(d, job)
        work.put(job_id)
    return redirect(url_for('job_page', job_id=job_id))


@app.route('/jobs/<job_id>/files/<name>')
def job_file(job_id, name):
    return send_from_directory(job_dir(job_id), secure_filename(name), max_age=86400)


start()
