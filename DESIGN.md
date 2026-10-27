# Captures, runs, marks and storage

## Terms
- **Capture**: what was photographed: the uploaded photos and the settings given with them (shelf category, overrides,
  minimum, note, second read). A capture never changes after it is made, except for its marks.
- **Run**: one pass of the pipeline over a capture, with the code as it stood: the readings, results and images, its cost, and
  the **baseline** it ran on. A capture has one or more runs; a new run can be started on an old capture at any time.
- **Baseline**: the code version a run used: `{"sha": "...", "committed_at": "2026-10-23T13:40:00+00:00", "dirty": false}`,
  or `null` for runs made before the code was in git (every migrated run).
- **Mark**: someone's verdict on one book in a capture: `worth` ("Worth it"), `not` ("Not worth it") or `should` ("Should
  pull", on a book the run didn't pull). A book with no mark is **unanswered**; clearing a mark makes it unanswered again.
  Marks belong to the capture, not to a run, so they carry over to new runs of the same capture.

## Storage layout
Everything lives in one store (Azure Blob Storage on Azure, a folder locally), under keys separated by `/`:

```
captures/index.json                                   summaries of every capture, newest first (see below)
captures/<cid>/capture.json                           the capture
captures/<cid>/photos/<file>                          the uploaded photos (upload1.jpg, ...)
captures/<cid>/marks.json                             the marks, keyed by mark id
captures/<cid>/runs/<rid>/run.json                    the run: status, progress, baseline, cost, counts
captures/<cid>/runs/<rid>/results.json                the results, as pipeline.run writes them today
captures/<cid>/runs/<rid>/files/<name>                annotated photos and spine crops
```

- Capture ids: 12 hex characters, as job ids are today (old job ids are kept as capture ids). Run ids sort by time:
  `YYYYMMDDTHHMMSSZ-xxxx` (UTC, then 4 hex characters).
- `capture.json`: `{"id", "created", "photos": [{"file", "name", "sha256", "category", "overrides", "minimum", "note", "second_read"}]}`.
  The photos' SHA-256s are the capture's identity: an upload of the same photos becomes a new run of the existing capture.
  The settings are the latest ones, used for the next run.
- `captures/aliases.json`: `{"<merged capture id>": "<capture it was merged into>"}`, written by tools/consolidate.py.
- `run.json`: `{"id", "capture_id", "created", "started", "finished", "status", "progress", "error", "cap", "cost",
  "baseline", "photos", "photos_read", "pulls", "leads", "spines", "unreadable"}`; `photos` are the settings the run used (runs from before
  they were kept fall back to the capture's). status: queued, running, done, failed, stopped.
- `marks.json`: `{"<mark id>": {"verdict", "label", "category", "value", "at", "run_id", "book": {"photo", "shelf", "x0",
  "x1", "y0", "y1", "title", "author"}}}`. The mark id is made when the mark is first set; `book` is the book as the run that
  was shown read it (photo is 1-based), so the mark can be matched to the same book in other runs.
- `captures/index.json`: `{"captures": [{"id", "created", "photos", "category", "latest_run": {"id", "status", "created",
  "pulls", "leads", "cost", "baseline"}, "runs": n, "total_cost": $ over every run, "marks": n}]}`, newest first. It is updated with ETag retries whenever a capture is
  made, a run changes status or a mark changes, so the scan page needs one read, not one per capture.
- Writes that can race (marks, the index) use the store's ETags: read with its etag, write with `if_match`, retry on
  `Conflict`.
- Kept outside the store, on the app's own disk, because they can be rebuilt: the Claude response cache, the spend ledger
  and the credit balance.

## Modules (who owns what)
- `shelfscan/store.py`: the store interface (`LocalStore`; `BlobStore` for Azure) and `open_store()`, which picks Azure when
  `AZURE_STORAGE_CONNECTION_STRING` is set (container `SHELFSCAN_CONTAINER`, default `shelfscan`), else a folder.
- `shelfscan/version.py`: `baseline()`, from `version.json` (written by deploy.sh) or from git when run from a checkout.
- `shelfscan/repo.py`: captures and runs on top of the store: create a capture, start a run, save run progress and results,
  list captures (via the index), read a run's files. Key helpers live here.
- `shelfscan/marks.py`: set and clear marks, and match a capture's marks to the books of any of its runs.
- `shelfscan/evaluate.py`: score a run against its capture's marks; re-run every marked capture with the current baseline.
- `shelfscan/pipeline.py`: unchanged in what it computes. The worker copies a capture's photos into a temporary folder, runs
  the pipeline there, then saves results and files to the run's keys.
- `tools/migrate_jobs.py`: copies old `jobs/<id>` folders into captures with one run each (baseline null), with their marks.

## Matching marks to books across runs
A mark matches a book in a run when both are in the same photo and on the same shelf (or neighbouring shelves when shelf
bands differ), their boxes overlap by at least half of the narrower box in x and a third in y, and their titles are similar
(or one is unread). The best-overlapping book wins; a mark with no match is reported as "book not found in this run".

## Scoring a run against marks
For each answered mark: a `worth` or `should` mark agrees when the run pulls the book (or lists it as a lead); a `not` mark
agrees when the run doesn't pull it. Unanswered books don't count. The report gives, per capture and in total: agreed,
disagreed, book not found, and unanswered pulls, so two baselines can be compared on the same marked captures.
