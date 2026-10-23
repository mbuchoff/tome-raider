"""Where captures, runs and marks are kept: a folder locally, Azure Blob Storage on Azure. Keys are '/'-separated paths.

Both stores give the same calls. ETags let racing writers (marks, the capture index) detect each other: read with the etag,
write with if_match, and retry on Conflict."""
import json, os, threading


class NotFound(Exception):
    pass


class Conflict(Exception):
    """The key changed since it was read (if_match), or already exists (if_none_match)."""


class LocalStore:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.lock = threading.Lock()
        os.makedirs(self.root, exist_ok=True)

    def _p(self, key):
        p = os.path.abspath(os.path.join(self.root, *key.split('/')))
        if not p.startswith(self.root + os.sep): raise ValueError(f'bad key: {key}')
        return p

    def _etag(self, p):
        st = os.stat(p)
        return f'{st.st_mtime_ns}-{st.st_size}'

    def get_bytes(self, key):
        try:
            with open(self._p(key), 'rb') as f: return f.read()
        except (FileNotFoundError, IsADirectoryError): raise NotFound(key)

    def put_bytes(self, key, data, content_type=None, if_match=None, if_none_match=False):
        """Returns the new etag."""
        p = self._p(key)
        with self.lock:
            exists = os.path.isfile(p)
            if if_none_match and exists: raise Conflict(key)
            if if_match is not None and (not exists or self._etag(p) != if_match): raise Conflict(key)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = f'{p}.{os.getpid()}.{threading.get_ident()}.tmp'
            with open(tmp, 'wb') as f: f.write(data)
            os.replace(tmp, p)
            return self._etag(p)

    def get_json(self, key):
        """(object, etag)."""
        p = self._p(key)
        with self.lock:
            try:
                with open(p, 'rb') as f: data = f.read()
                return json.loads(data), self._etag(p)
            except (FileNotFoundError, IsADirectoryError): raise NotFound(key)

    def put_json(self, key, obj, if_match=None, if_none_match=False):
        return self.put_bytes(key, json.dumps(obj, indent=1, default=str).encode(), 'application/json', if_match, if_none_match)

    def exists(self, key):
        return os.path.isfile(self._p(key))

    def list(self, prefix):
        """Every key under prefix, sorted."""
        base = self._p(prefix.rstrip('/')) if prefix.strip('/') else self.root
        out = []
        for d, _, files in os.walk(base):
            for f in files:
                if not f.endswith('.tmp'): out.append(os.path.relpath(os.path.join(d, f), self.root).replace(os.sep, '/'))
        return sorted(out)

    def children(self, prefix):
        """The names one level below prefix ('captures/' -> capture ids), sorted."""
        base = self._p(prefix.rstrip('/')) if prefix.strip('/') else self.root
        try: return sorted(os.listdir(base))
        except FileNotFoundError: return []

    def delete(self, key):
        try: os.remove(self._p(key))
        except FileNotFoundError: pass

    def delete_prefix(self, prefix):
        for k in self.list(prefix): self.delete(k)


def open_store(local_root=None):
    """Azure Blob Storage when AZURE_STORAGE_CONNECTION_STRING is set (container SHELFSCAN_CONTAINER, default 'shelfscan'),
    else a folder: local_root, or ./var/store next to the app."""
    if os.environ.get('AZURE_STORAGE_CONNECTION_STRING'):
        raise NotImplementedError('BlobStore is not written yet')
    return LocalStore(local_root or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'var', 'store'))
