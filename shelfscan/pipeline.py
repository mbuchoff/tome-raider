"""Runs one job: every photo through shelf finding, reading, the rules and the result images.

A job is a folder holding job.json (the request and its status), the uploaded photos, and after a run results.json plus images."""
import concurrent.futures as cf, datetime, json, os, re, threading, time, traceback
from . import images, notes, reader, rules
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


def run(job_dir, claude, now=time.time):
    job = load_job(job_dir)
    job.update(status='running', started=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), error=None)
    save_job(job_dir, job)
    t0 = now()
    budget = Budget(job.get('cap', 2.0))
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
            save_job(job_dir, job)

    def one_photo(k, p):
        progress(k, 'finding shelves')
        im = images.load(os.path.join(job_dir, p['file']))
        overrides = {int(s): c for s, c in (p.get('overrides') or {}).items()}
        def shelf_category(s): return overrides.get(s, p['category'])
        def minimum_for(cat): return p.get('minimum') or ref.rules['minimum_value'][cat]
        def wants_second(s): return bool(p.get('second_read')) and shelf_category(s) in ('rare', 'extreme', 'signed')
        def note(s): return reader.STICKER_NOTE if shelf_category(s) in rules.CHECK_ORDER else ''
        bands, books = reader.read_photo(claude, im, budget, wants_second, progress=lambda d, n, msg=None: progress(k, msg or f'read {d} of {n} crops'),
                                         note=note)
        pulls, leads, not_pulled, skipped = rules.evaluate(books, shelf_category, ref, minimum_for)
        # the feet of the candidates' spines, up close: book-club logos and reprint imprints there are easy to miss at shelf scale
        progress(k, 'checking the feet of the candidate spines')
        if reader.check_feet(claude, im, [c['book'] for x in pulls + leads for c in [x] + x['copies']], budget):
            pulls, leads, not_pulled, skipped = rules.evaluate(books, shelf_category, ref, minimum_for)
        progress(k, 'writing notes on the candidates')
        try: notes.add_notes(claude, pulls + leads, budget)
        except Exception as e: print(f'photo {k}: no notes ({type(e).__name__}: {e})', flush=True)     # the notes are extra; keep the results
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
        return dict(
            name=p.get('name') or p['file'], file=p['file'], note=p.get('note', ''), category=p['category'],
            shelves=[{'shelf': s, 'category': shelf_category(s), 'minimum': minimum_for(shelf_category(s)), 'check_order': shelf_category(s) in rules.CHECK_ORDER,
                      'books': sum(1 for b in books if b['shelf'] == s), 'unreadable': sum(1 for b in unread if b['shelf'] == s)}
                     for s in shelves],
            annotated=f'{stem}_annotated.jpg', books=len(books), unreadable=len(unread), pulls=pulls, leads=leads, not_pulled=not_pulled, skipped=skipped,
            second_read=bool(p.get('second_read')), readings=books)       # every reading, so the rules can be replayed for free

    # photos run side by side (each already reads four crops at a time), so a three-photo scan takes about as long as one;
    # a photo that fails or hits the cap doesn't lose the others' results
    with cf.ThreadPoolExecutor(min(3, len(job['photos']))) as ex:
        futures = [ex.submit(one_photo, k, p) for k, p in enumerate(job['photos'], 1)]
    final, error = 'done', None            # job.json keeps saying 'running' until every photo has finished
    for k, f in enumerate(futures, 1):
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
    save_job(job_dir, job)
    return job, results
