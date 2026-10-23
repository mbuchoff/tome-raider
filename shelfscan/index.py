"""captures/index.json: one summary per capture (see DESIGN.md), so the scan page reads one key. Placeholder until the
storage work lands; callers already use these names."""


def update_entry(store, cid):
    """Recomputes one capture's summary from its capture.json, latest run.json and marks.json, with ETag retries."""


def read(store):
    """{'captures': [...]}, newest first."""
    return {'captures': []}


def rebuild(store):
    """Recomputes every summary from the captures in the store."""
