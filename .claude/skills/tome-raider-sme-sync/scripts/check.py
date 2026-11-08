"""Checks page.html and page.json before publishing: it renders (so every disagreement is covered and every placeholder
resolves), its script parses, nothing he ticked done is still on the page, no answered call is left as a call, and prices
typed into the prose next to a book's title match what the replay gives that book. Also checks email.md if the run has one.
Errors exit non-zero; warnings need a look but don't block.
usage: python check.py RUN_DIR"""
import glob, os, re, shutil, subprocess, sys, tempfile
from common import load_config, load_json, path
from render import Problem, render

run_dir = sys.argv[1]
cfg = load_config()
rep, state, page = load_json(os.path.join(run_dir, 'replay.json')), load_json(path('state.json'), {}), load_json(path('page.json'))
rows = rep['rows']
errors, warns = [], []
try:
    html = render(page, rows, state.get('score') or {}, load_json(path('data.json'), {}), open(path('shell.html'), encoding='utf-8').read())
except Problem as e:
    sys.exit(f'ERROR: does not render: {e}')
if not os.path.exists(path('page.html')) or open(path('page.html'), encoding='utf-8').read() != html:
    errors.append('page.html is not the current render of page.json: run render.py')
if '{{' in html: errors.append('an unfilled {{placeholder}} is left in the page')
for need in ('id="banner"', 'id="ack-count"'):
    if need not in html: errors.append(f'the page lacks {need}, which its script needs')
if len(html.encode()) > 15_000_000: errors.append('the page is over 15 MB')

js = re.search(r'<script>(.*)</script>', html, re.S)
if shutil.which('node') and js:
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f: f.write(js.group(1))
    r = subprocess.run(['node', '--check', f.name], capture_output=True, text=True)
    os.unlink(f.name)
    if r.returncode: errors.append('the page script does not parse: ' + r.stderr.strip().splitlines()[-1])
elif not shutil.which('node'): warns.append('node not found: the page script was not syntax-checked')

acks = {os.path.basename(p)[:-5]: load_json(p) for p in glob.glob(os.path.join(run_dir, 'db', 'acks', '*.json'))}
answers = {os.path.basename(p)[:-5]: load_json(p) for p in glob.glob(os.path.join(run_dir, 'db', 'answers', '*.json'))}
for it in page['items']:
    if (acks.get(it['id']) or {}).get('done') and not it.get('keep'):
        errors.append(f'{it["id"]} was ticked done but is still on the page (remove it, or set "keep": true with a reason in the item)')
    if it['kind'] == 'call' and (answers.get(it['id']) or {}).get('choice') and not it.get('keep'):
        warns.append(f'{it["id"]} is answered ({answers[it["id"]]["choice"]!r}) but still a call: act on it, or say on the page what happens next and set "keep": true')

# a price typed next to a title: <i>Title</i> ... $N within the same sentence
main = re.sub(r'<script>.*</script>', '', html, flags=re.S)
for m in re.finditer(r'<i>([^<]{3,80})</i>([^<.;]{0,90}?)\$([\d,]+)', main):
    title, n = m.group(1).strip(), int(m.group(3).replace(',', ''))
    hit = [r for r in rows if r['label'].lower().startswith(title.lower()) or r['label'].lower().startswith('the ' + title.lower())]
    vals = [r['value'] for r in hit if r['value'] is not None]
    if vals and not any(abs(v - n) <= max(1.0, 0.02 * v) for v in vals):
        warns.append(f'"{title}" … ${n:,} on the page, but the replay values it at ' + ', '.join(f'${round(v):,}' for v in sorted(set(vals))))

email = os.path.join(run_dir, 'email.md')
if os.path.exists(email):
    t = open(email, encoding='utf-8').read()
    if cfg['artifact_url'] not in t: errors.append('email.md lacks the page link')
    if '{{' in t: errors.append('email.md has an unfilled placeholder')
    if not t.lower().startswith('subject:'): errors.append('email.md must start with a "Subject:" line')

for x in warns: print('WARNING:', x)
for x in errors: print('ERROR:', x)
print('ok' if not errors else f'{len(errors)} error(s)')
sys.exit(1 if errors else 0)
