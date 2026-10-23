"""Routes for marks and scoring (see DESIGN.md), as a Flask blueprint the app registers. Placeholder until the marks work
lands: POST /captures/<cid>/marks sets or clears a mark; /marks.json lists them; /eval scores runs against marks.

The app sets app.config['START_RUN'] = function(cid) -> run id, so /eval can re-run marked captures."""
from flask import Blueprint, abort

bp = Blueprint('marks', __name__)


@bp.route('/captures/<cid>/marks', methods=['POST'])
def set_mark(cid):
    abort(501)
