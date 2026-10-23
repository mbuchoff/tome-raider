"""The Collections page: the SME answers which story collections, anthologies and omnibuses to keep (the whitelist), with
optional notes, and can add titles by hand. Answers take effect for the next run (shelfscan.anthologies)."""
from flask import Blueprint, abort, redirect, render_template, request, url_for
from . import anthologies, pipeline
from .store import Conflict

bp = Blueprint('collections', __name__)


def collections():
    return pipeline.reference().collections


@bp.route('/collections')
def page():
    c = collections()
    works = c.catalog()
    for w in works: w['key'] = anthologies.key(w['work'], w.get('author'))
    covered = sorted((w for w in works if w['kind'] in anthologies.KINDS), key=lambda w: -(w.get('median') or 0))
    others = sorted((w for w in works if w['kind'] not in anthologies.KINDS), key=lambda w: -(w.get('median') or 0))
    a = c.answers()
    return render_template('collections.html', covered=covered, others=others, answers=a['decisions'], added=a['added'],
                           defaults=c.defaults, whitelist=c.whitelist(), kind_names=anthologies.KIND_NAMES)


@bp.route('/collections/answer', methods=['POST'])
def answer():
    """JSON {key, work, author, decision, note}: decision 'yes', 'no' or '' (unanswered); leaving decision or note out keeps it."""
    d = request.get_json(silent=True) or {}
    k, work = str(d.get('key') or '')[:300], str(d.get('work') or '')[:200]
    decision = d.get('decision') if 'decision' in d else None
    if not k or not work or decision not in (None, '', 'yes', 'no'): abort(400)
    try: r = collections().answer(k, work, str(d.get('author') or '')[:120], decision, d.get('note') if 'note' in d else None)
    except Conflict: abort(409)
    return {'ok': True, 'decision': r.get('decision', ''), 'note': r.get('note', ''), 'whitelisted': len(collections().whitelist())}


@bp.route('/collections/add', methods=['POST'])
def add():
    try: collections().add(request.form.get('work', ''), request.form.get('author', ''), request.form.get('note', ''))
    except Conflict: abort(409)
    return redirect(url_for('collections.page') + '#added')


@bp.route('/collections/remove', methods=['POST'])
def remove():
    try: collections().add('', '', remove_id=request.form.get('id', ''))
    except Conflict: abort(409)
    return redirect(url_for('collections.page') + '#added')
