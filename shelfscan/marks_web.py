"""Routes for marks and scoring (see DESIGN.md), as a Flask blueprint the app registers: POST /captures/<cid>/marks sets
or clears a mark, /marks.json lists them, /eval scores runs against them and re-runs the marked captures.

The app sets app.config['STORE'] (the store) and app.config['START_RUN'] = function(cid) -> run id."""
import re
from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for
from . import evaluate, marks, repo
from .store import Conflict, NotFound, open_store

bp = Blueprint('marks', __name__)
RUN_ID = re.compile(r'^[0-9A-Za-z-]{1,40}$')


def store():
    s = current_app.config.get('STORE')
    if s is None: s = current_app.config['STORE'] = open_store()
    return s


@bp.route('/captures/<cid>/marks', methods=['POST'])
def set_mark(cid):
    """JSON {run_id, key, verdict, note}; verdict 'worth', 'not' or 'should', or '' to clear it (unanswered); note is optional
    text, '' to remove it. Leaving either out keeps it as it is."""
    data = request.get_json(silent=True) or {}
    run_id, key = str(data.get('run_id') or ''), str(data.get('key') or '')[:80]
    verdict = (data.get('verdict') or '') if 'verdict' in data else marks.KEEP
    note = str(data.get('note') or '') if 'note' in data else marks.KEEP
    if not cid.isalnum() or not RUN_ID.match(run_id) or not key: abort(400)
    if verdict is not marks.KEEP and verdict not in ('',) + marks.VERDICTS: abort(400)
    try: results = store().get_json(repo.results_key(cid, run_id))[0]
    except NotFound: abort(404)
    try: r = marks.set_mark(store(), cid, run_id, key, verdict, results, note=note)
    except KeyError: abort(404)
    except Conflict: abort(409)
    return {'ok': True, 'verdict': r['verdict'], 'note': r['note'], 'marked': r['marked']}


@bp.route('/marks.json')
@bp.route('/feedback.json')
def all_marks():
    """Every mark across all captures, for tuning the rules."""
    return {'marks': marks.all_marks(store())}


@bp.route('/eval')
def eval_page():
    return render_template('eval.html', summary=evaluate.summary(store()), can_rerun=bool(current_app.config.get('START_RUN')))


@bp.route('/eval/rerun', methods=['POST'])
def rerun_marked():
    """A new run, with the current baseline, of every capture that has marks."""
    start = current_app.config.get('START_RUN')
    if not start: abort(501)
    for cid in marks.capture_ids(store()):
        if marks.load(store(), cid)[0]: start(cid)
    return redirect(url_for('marks.eval_page'))
