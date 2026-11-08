"""Runs one job: every photo through shelf finding, reading, the rules and the result images.

A job is a folder holding job.json (the request and its status), the uploaded photos, and after a run results.json plus images.
run_stored() runs a stored capture this way in a temporary folder and saves the run's results and images to the store."""
import concurrent.futures as cf, datetime, json, os, re, shutil, tempfile, threading, time, traceback
from . import anthologies, catalogs, edition_check, images, notes, reader, repo, rules, scan_note
from .claude import Budget, CapReached, Claude
from .refdata import Reference

_ref, _ref_lock = None, threading.Lock()
UNREADABLE_COLOR = '#ffb000'          # amber, for the '?' boxes; red is for pulls and blue for leads


def reference():
    global _ref
    with _ref_lock:
        if _ref is None: _ref = Reference()
    return _ref


def load_job(job_dir):
    return json.load(open(os.path.join(job_dir, 'job.json')))


def save_job(job_dir, job):
    tmp = os.path.join(job_dir, f'job.json.{os.getpid()}.{threading.get_ident()}.tmp')
    json.dump(job, open(tmp, 'w'), indent=1)
    os.replace(tmp, os.path.join(job_dir, 'job.json'))


def parse_overrides(text):
    """'3=signed, 1-2 rare' -> {3: 'signed', 1: 'rare', 2: 'rare'}"""
    out = {}
    for a, b, cat in re.findall(r'(\d+)\s*(?:-\s*(\d+))?\s*[=:]?\s*(thrift|cheap|rare|extreme|signed)', (text or '').lower()):
        for s in range(int(a), int(b or a) + 1): out[s] = cat
    return out


def parse_publishers(text):
    """The shelves the same field says hold one publisher's books, among those whose sales value only their own copies
    (Easton Press, Franklin Library, the specialty publishers): '4=Easton Press, 5-6 franklin' -> {4: 'Easton Press', 5:
    'Franklin Library', 6: 'Franklin Library'}."""
    out = {}
    for a, b, what in re.findall(r'(\d+)\s*(?:-\s*(\d+))?\s*[=:]?\s*([^,;\d]+)', text or ''):
        house = rules.special_house(re.sub(r'\bfranklin\b(?!\s+(?:library|mint))', 'Franklin Library', what, flags=re.I))
        if house:
            for s in range(int(a), int(b or a) + 1): out[s] = house
    return out


def publisher_hints(p, bands, books):
    """Sets book['publisher_hint'] for the books on a shelf the user's setting, or else a sign on the shelf, gives a publisher
    (the rules use it when the spine names none). The scan's note can name one too (scan_note), over a sign's."""
    setting = {int(s): h for s, h in (p.get('publishers') or {}).items()}
    sign = {x['shelf']: x['sign'] for x in bands if rules.special_house(x.get('sign'))}
    for b in books:
        if setting.get(b['shelf']): b['publisher_hint'] = {'publisher': setting[b['shelf']], 'from': 'the shelf setting'}
        elif b['shelf'] in sign: b['publisher_hint'] = {'publisher': rules.special_house(sign[b['shelf']]), 'from': f"the shelf's sign (\"{sign[b['shelf']]}\")"}


def run(job_dir, claude, now=time.time, on_progress=None):
    """on_progress(job): called after each change of status or progress, as job.json is saved."""
    def save(job):
        save_job(job_dir, job)
        if on_progress: on_progress(job)

    job = load_job(job_dir)
    job.update(status='running', started=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), error=None)
    save(job)
    t0 = now()
    budget = Budget(job.get('cap', 2.0), fresh_since=job.get('fresh_since'))
    parts = [budget.part() for _ in job['photos']]       # one per photo, in order, so the cost says what each photo cost
    ref = reference()
    results = {'photos': [], 'version': ref.version()}
    lock = threading.Lock()
    status = {}                                         # photo number -> what it's doing, for the progress line

    def progress(k, msg):
        with lock:
            status[k] = msg
            n = len(job['photos'])
            job['progress'] = '; '.join((f'photo {i}: ' if n > 1 else '') + status[i] for i in sorted(status))
            job['cost'] = budget.summary()
            save(job)

    def settings(p):
        overrides = {int(s): c for s, c in (p.get('overrides') or {}).items()}
        def shelf_category(s): return overrides.get(s, p['category'])
        def minimum_for(cat): return p.get('minimum') or ref.rules['minimum_value'][cat]
        return shelf_category, minimum_for

    def read_one(k, p):
        budget = parts[k - 1]                           # this photo's calls spend through its part of the job's budget
        progress(k, 'finding shelves')
        im = images.load(os.path.join(job_dir, p['file']))
        shelf_category, _ = settings(p)
        def wants_second(s): return bool(p.get('second_read')) and shelf_category(s) in ('rare', 'extreme', 'signed')
        def note(s): return reader.STICKER_NOTE if shelf_category(s) in rules.CHECK_ORDER else ''
        bands, books = reader.read_photo(claude, im, budget, wants_second, progress=lambda d, n, msg=None: progress(k, msg or f'read {d} of {n} crops'),
                                         note=note)
        return im, bands, books

    def one_photo(k, p, im, bands, books, note_applied):
        budget = parts[k - 1]
        shelf_category, minimum_for = settings(p)
        # a book with no author whose title found no sales is looked up in free catalogs; an author found there is used
        # like one read on the spine
        progress(k, 'looking up authors')
        try: catalogs.add_authors(books, ref)
        except Exception as e:                  # the lookups are extra; keep the results
            print(f'photo {k}: no author lookups ({type(e).__name__}: {e})', flush=True)
        wants = []          # leatherbound copies with no publisher on the spine, to compare with their title's sale photos
        pulls, leads, not_pulled, skipped = rules.evaluate(books, shelf_category, ref, minimum_for, wants)
        # the feet of the candidates' spines, up close: book-club logos and reprint imprints there are easy to miss at shelf scale
        progress(k, 'checking the feet of the candidate spines')
        if reader.check_feet(claude, im, [c['book'] for x in pulls + leads for c in [x] + x['copies']], budget):
            wants = []
            pulls, leads, not_pulled, skipped = rules.evaluate(books, shelf_category, ref, minimum_for, wants)
        # which edition each of those is, from the sale photos: it's valued only from the sales that match
        if wants:
            progress(k, 'comparing leather spines with sale photos')
            if edition_check.run(claude, im, wants, budget): pulls, leads, not_pulled, skipped = rules.evaluate(books, shelf_category, ref, minimum_for)
        # when each candidate copy was printed, from its whole spine, so it's valued from sales of its own years
        progress(k, 'dating the candidate copies')
        try: dated = reader.date_copies(claude, im, [c['book'] for x in pulls + leads for c in [x] + x['copies']], budget)
        except Exception as e:                  # the dates are extra; keep the results
            dated = 0
            print(f'photo {k}: no dates ({type(e).__name__}: {e})', flush=True)
        if dated: pulls, leads, not_pulled, skipped = rules.evaluate(books, shelf_category, ref, minimum_for)
        progress(k, 'writing notes on the candidates')
        try: written = notes.add_notes(claude, pulls + leads, budget)
        except Exception as e:                  # the notes are extra; keep the results
            written = 0
            print(f'photo {k}: no notes ({type(e).__name__}: {e})', flush=True)
        # what the notes found (the author, who a book is about, the copy's publisher, a collection) can change a decision
        if written or any(ref.collections.kind(x['book'])[0] for x in pulls + leads):
            pulls, leads, not_pulled, skipped = rules.evaluate(books, shelf_category, ref, minimum_for)
        # pulls and leads valued from first-edition sales: their spines against those sales' photos, for a line on the card
        # when one looks like another edition (or like the first); the values stay as they are
        if any(c['assessment'].get('on_firsts') and 'first_photos' not in c['book'] for x in pulls + leads for c in [x] + x['copies']):
            firsts = []
            pulls, leads, not_pulled, skipped = rules.evaluate(books, shelf_category, ref, minimum_for, firsts=firsts)
            progress(k, 'comparing spines with first-edition photos')
            if firsts and edition_check.run(claude, im, firsts, budget, how=edition_check.compare_first):
                pulls, leads, not_pulled, skipped = rules.evaluate(books, shelf_category, ref, minimum_for)
        stem = f'photo{k}'
        # spines nothing could be read from get a '?' box, drawn first so the numbered boxes sit on top
        unread = [b for b in books if rules.unreadable(b)]
        boxes = [('?', b['x0'], b['y0'], b['x1'], b['y1'], UNREADABLE_COLOR, 2) for b in unread]
        boxes += [(x['n'], c['book']['x0'], c['book']['y0'], c['book']['x1'], c['book']['y1'], color, 3)
                  for xs, color in ((leads, '#1e5be0'), (pulls, '#e01e1e')) for x in xs for c in [x] + x['copies']]
        open(os.path.join(job_dir, f'{stem}_annotated.jpg'), 'wb').write(images.annotate(im, boxes))
        for x in pulls + leads:
            for j, c in enumerate([x] + x['copies']):
                b = c['book']
                pad = (b['x1'] - b['x0']) * 0.6 + 20
                crop = im.crop((max(0, b['x0'] - pad), b['y0'], min(im.size[0], b['x1'] + pad), b['y1']))
                crop.thumbnail((360, 260) if j == 0 else (180, 130))
                c['thumb'] = f"{stem}_pull{x['n']}" + (f'_{j}' if j else '') + '.jpg'
                open(os.path.join(job_dir, c['thumb']), 'wb').write(images.jpeg(crop, 85))
        progress(k, 'done')
        shelves = sorted({b['shelf'] for b in bands})
        signs = {x['shelf']: x['sign'] for x in bands if x.get('sign')}      # a sign on the shelf, as read
        return dict(
            name=p.get('name') or p['file'], file=p['file'], note=p.get('note', ''), category=p['category'],
            shelves=[{'shelf': s, 'category': shelf_category(s), 'minimum': minimum_for(shelf_category(s)), 'check_order': shelf_category(s) in rules.CHECK_ORDER,
                      'books': sum(1 for b in books if b['shelf'] == s), 'unreadable': sum(1 for b in unread if b['shelf'] == s),
                      **({'sign': signs[s]} if s in signs else {})}
                     for s in shelves],
            annotated=f'{stem}_annotated.jpg', books=len(books), unreadable=len(unread), pulls=pulls, leads=leads, not_pulled=not_pulled, skipped=skipped,
            second_read=bool(p.get('second_read')), readings=books,       # every reading, so the rules can be replayed for free
            **({'note_applied': note_applied} if note_applied else {}))

    # photos run side by side (each already reads four crops at a time), so a three-photo scan takes about as long as one;
    # a photo that fails or hits the cap doesn't lose the others' results. Every photo is read before any is assessed, since
    # the scan's note can name a section on any of them (scan_note); its call counts toward the scan, not a photo.
    photos = list(enumerate(job['photos'], 1))
    with cf.ThreadPoolExecutor(min(3, len(photos))) as ex:
        reads = {k: ex.submit(read_one, k, p) for k, p in photos}
    read = {k: f.result() for k, f in reads.items() if not f.exception()}
    for k, p in photos:
        if k in read: publisher_hints(p, read[k][1], read[k][2])
    applied = {}
    noted = [(k, p.get('name') or p['file'], p.get('note'), read[k][2]) for k, p in photos if k in read and (p.get('note') or '').strip()]
    if noted:
        for k, *_ in noted: progress(k, 'applying the note')
        try: applied = scan_note.apply(claude, noted, budget)
        except Exception as e:                  # the note is extra; keep the results, and say it wasn't applied
            applied = {k: {'error': f'{type(e).__name__}: {e}'} for k, *_ in noted}
            print(f'note not applied ({type(e).__name__}: {e})', flush=True)
    with cf.ThreadPoolExecutor(min(3, len(photos))) as ex:
        futures = {k: ex.submit(one_photo, k, p, *read[k], applied.get(k)) for k, p in photos if k in read}
    final, error = 'done', None            # job.json keeps saying 'running' until every photo has finished
    for k, _ in photos:
        f = futures.get(k) or reads[k]          # a photo that failed in reading has its reading's error
        try:
            results['photos'].append(f.result())
        except CapReached as e:
            final, error = 'stopped', f'photo {k}: {e}'
        except Exception as e:
            if final != 'stopped': final, error = 'failed', f'photo {k}: {type(e).__name__}: {e}'
            traceback.print_exception(e)
    results['cost'] = budget.summary()
    results['seconds'] = round(now() - t0, 1)
    tmp = os.path.join(job_dir, f'results.json.{os.getpid()}.tmp')        # written whole or not at all
    json.dump(results, open(tmp, 'w'), indent=1, default=str)
    os.replace(tmp, os.path.join(job_dir, 'results.json'))
    job['status'], job['error'] = final, error
    job['cost'], job['progress'] = budget.summary(), ''
    job['finished'] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    save(job)
    return job, results


def run_stored(store, cid, rid, claude, work_root):
    """Runs a stored capture: copies its photos into a temporary folder under work_root, runs the pipeline there, mirrors
    progress into run.json (at most every 2 seconds), then saves results.json and every image to the run's keys."""
    capture, run_ = repo.load_capture(store, cid), repo.load_run(store, cid, rid)
    os.makedirs(work_root, exist_ok=True)
    d = tempfile.mkdtemp(prefix=f'{cid}-', dir=work_root)
    last = [0.0]

    def mirror(job):        # progress only: the final status is saved once the results and images are in the store
        if job['status'] != 'running' or time.time() - last[0] < 2: return
        last[0] = time.time()
        changed = run_['status'] != job['status']
        run_.update({k: job.get(k) for k in ('status', 'started', 'progress', 'cost', 'error')})
        repo.save_run(store, run_, update_index=changed)

    try:
        for p in capture['photos']:
            with open(os.path.join(d, p['file']), 'wb') as f: f.write(repo.get_photo(store, cid, p['file']))
        fresh = datetime.datetime.fromisoformat(run_['created']).timestamp() if run_.get('reread') else None   # read again, from when it was queued
        save_job(d, dict(id=cid, created=capture['created'], status='queued', cap=run_.get('cap') or 2.0, photos=repo.run_photos(capture, run_),
                         fresh_since=fresh))
        job, results = run(d, claude, on_progress=mirror)
        uploads = {p['file'] for p in capture['photos']} | {'job.json', 'results.json'}
        for name in sorted(os.listdir(d)):
            if name not in uploads and not name.endswith('.tmp'):
                with open(os.path.join(d, name), 'rb') as f: repo.put_run_file(store, cid, rid, name, f.read())
        store.put_json(repo.results_key(cid, rid), results)        # after the images, so a run with results has its images
        run_.update(status=job['status'], error=job.get('error'), progress='', cost=job.get('cost'), finished=job.get('finished'),
                    **repo.run_counts(results))
    except Exception as e:
        run_.update(status='failed', error=f'{type(e).__name__}: {e}', progress='', finished=repo.now_iso())
        traceback.print_exception(e)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    repo.save_run(store, run_, update_index=True)
    return run_
