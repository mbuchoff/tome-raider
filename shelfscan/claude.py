"""Claude API calls with a response cache, cost tracking and a spending cap.

Responses are cached by a hash of the model, prompt and image, so re-running a photo never pays twice.
Before each call the worst-case cost is reserved against the job's cap, using the free token count. Each photo spends through
its own part of the job's budget, so the run can say what every photo cost."""
import base64, hashlib, json, os, threading, time
import anthropic

SONNET, OPUS = 'claude-sonnet-5-5', 'claude-opus-5-5'
PRICE = {SONNET: (2.0, 10.0), OPUS: (4.0, 20.0)}         # $ per million input / output tokens


class CapReached(Exception):
    pass


def cost(model, usage):
    pin, pout = PRICE[model]
    return (usage['input_tokens'] * pin + usage['output_tokens'] * pout) / 1e6


class Budget:
    """Spending for one job, with a cap. Thread-safe."""
    def __init__(self, cap):
        self.cap, self.spent, self.reserved, self.calls, self.cached = cap, 0.0, 0.0, 0, 0
        self.tokens = {'input': 0, 'output': 0}
        self.lock = threading.Lock()
        self.parts = []

    def part(self):
        """A part of this budget for one photo: it spends against the same cap and keeps its own totals."""
        p = Part(self)
        with self.lock: self.parts.append(p)
        return p

    def reserve(self, worst):
        with self.lock:
            if self.spent + self.reserved + worst > self.cap:
                raise CapReached(f'stopped before a call that could take this job past its ${self.cap:.2f} cap (spent ${self.spent:.2f})')
            self.reserved += worst

    def release(self, worst):
        """Gives back a reservation whose call failed."""
        with self.lock: self.reserved -= worst

    def hit(self):
        """Counts a call answered from the cache."""
        with self.lock: self.cached += 1

    def settle(self, worst, rec):
        with self.lock:
            self.reserved -= worst
            self.spent += rec['cost']; self.calls += 1
            self.tokens['input'] += rec['usage']['input_tokens']; self.tokens['output'] += rec['usage']['output_tokens']

    def summary(self):
        with self.lock:
            s = {'cap': self.cap, 'spent': round(self.spent, 4), 'calls': self.calls, 'cached_calls': self.cached, 'tokens': dict(self.tokens)}
            if self.parts: s['photos'] = [{'spent': round(p.spent, 4), 'calls': p.calls, 'cached_calls': p.cached} for p in self.parts]
        return s


class Part:
    """One photo's part of a Budget: the job's cap, the photo's own spending. Takes the same calls as a Budget."""
    def __init__(self, budget):
        self.budget, self.spent, self.calls, self.cached = budget, 0.0, 0, 0

    def reserve(self, worst): self.budget.reserve(worst)

    def release(self, worst): self.budget.release(worst)

    def hit(self):
        self.budget.hit()
        with self.budget.lock: self.cached += 1

    def settle(self, worst, rec):
        self.budget.settle(worst, rec)
        with self.budget.lock: self.spent += rec['cost']; self.calls += 1


class Claude:
    def __init__(self, cache_dir, api_key=None, ledger=None):
        """ledger: a file that gets one JSON line per paid call ({"at": unix time, "cost": dollars}), for the credit figure."""
        self.cache_dir, self.ledger, self.ledger_lock = cache_dir, ledger, threading.Lock()
        os.makedirs(cache_dir, exist_ok=True)
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get('ANTHROPIC_API_KEY'), max_retries=4)

    def ask(self, model, image_jpeg, prompt, max_tokens, worst_out, budget):
        """One image (or None) plus one prompt; returns the response text."""
        key = hashlib.sha256(model.encode() + b'\0' + prompt.encode() + b'\0' + (image_jpeg or b'')).hexdigest()
        path = os.path.join(self.cache_dir, key[:2], key + '.json')
        if os.path.exists(path):
            budget.hit()
            return json.load(open(path))['text']
        image = [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.standard_b64encode(image_jpeg).decode()}}] if image_jpeg else []
        messages = [{'role': 'user', 'content': image + [{'type': 'text', 'text': prompt}]}]
        kw = dict(model=model, max_tokens=max_tokens, messages=messages)
        if model == SONNET: kw['thinking'] = {'type': 'between_tools'}     # transcription needs no thinking; Opus 5.5 always thinks
        n_in = self.client.messages.count_tokens(model=model, messages=messages).input_tokens       # free
        worst = cost(model, {'input_tokens': n_in, 'output_tokens': worst_out})
        budget.reserve(worst)
        t0 = time.time()
        try:
            with self.client.messages.stream(**kw) as s: msg = s.get_final_message()
        except Exception:
            budget.release(worst)
            raise
        u = {'input_tokens': msg.usage.input_tokens, 'output_tokens': msg.usage.output_tokens}
        rec = dict(model=model, seconds=round(time.time() - t0, 1), usage=u, cost=cost(model, u), stop=msg.stop_reason,
                   text=''.join(b.text for b in msg.content if b.type == 'text'))
        budget.settle(worst, rec)
        if self.ledger:
            with self.ledger_lock, open(self.ledger, 'a') as f: f.write(json.dumps({'at': round(time.time(), 1), 'cost': rec['cost']}) + '\n')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        json.dump(rec, open(tmp, 'w')); os.replace(tmp, path)
        return rec['text']
