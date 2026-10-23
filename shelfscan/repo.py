"""Captures and runs on top of the store (see DESIGN.md). Key helpers are fixed here so every module agrees on them."""
import datetime, secrets


def capture_key(cid): return f'captures/{cid}/capture.json'
def photo_key(cid, name): return f'captures/{cid}/photos/{name}'
def marks_key(cid): return f'captures/{cid}/marks.json'
def run_key(cid, rid): return f'captures/{cid}/runs/{rid}/run.json'
def results_key(cid, rid): return f'captures/{cid}/runs/{rid}/results.json'
def run_file_key(cid, rid, name): return f'captures/{cid}/runs/{rid}/files/{name}'
INDEX_KEY = 'captures/index.json'


def new_capture_id():
    return secrets.token_hex(6)


def new_run_id(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(2)
