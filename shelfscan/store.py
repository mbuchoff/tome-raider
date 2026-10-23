"""Where captures, runs and marks are kept: a folder locally, Azure Blob Storage on Azure. Keys are '/'-separated paths.

Both stores give the same calls. ETags let racing writers (marks, the capture index) detect each other: read with the etag,
write with if_match, and retry on Conflict."""
import hashlib, json, mimetypes, os, threading


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

    @staticmethod
    def _etag(data):
        # a hash of the contents: modification times are too coarse on some disks to tell two quick writes apart
        return hashlib.sha1(data).hexdigest()

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
            if if_match is not None:
                if not exists: raise Conflict(key)
                with open(p, 'rb') as f:
                    if self._etag(f.read()) != if_match: raise Conflict(key)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = f'{p}.{os.getpid()}.{threading.get_ident()}.tmp'
            with open(tmp, 'wb') as f: f.write(data)
            os.replace(tmp, p)
            return self._etag(data)

    def get_json(self, key):
        """(object, etag)."""
        p = self._p(key)
        with self.lock:
            try:
                with open(p, 'rb') as f: data = f.read()
                return json.loads(data), self._etag(data)
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
        try: return sorted(n for n in os.listdir(base) if not n.endswith('.tmp'))
        except FileNotFoundError: return []

    def delete(self, key):
        """Removes the key, and any folders it leaves empty, as a blob store has no empty folders."""
        p = self._p(key)
        with self.lock:
            try: os.remove(p)
            except FileNotFoundError: return
            d = os.path.dirname(p)
            while d != self.root:
                try: os.rmdir(d)
                except OSError: break
                d = os.path.dirname(d)

    def delete_prefix(self, prefix):
        for k in self.list(prefix): self.delete(k)


class BlobStore:
    """The same calls on an Azure Blob Storage container. prefix puts every key under a folder (used by the live test)."""
    def __init__(self, connection_string, container='shelfscan', prefix=''):
        from azure.storage.blob import BlobServiceClient
        self.container = BlobServiceClient.from_connection_string(connection_string).get_container_client(container)
        self.prefix = prefix

    def _b(self, key):
        if not key or key.startswith('/') or '..' in key.split('/'): raise ValueError(f'bad key: {key}')
        return self.container.get_blob_client(self.prefix + key)

    def get_bytes(self, key):
        from azure.core.exceptions import ResourceNotFoundError
        try: return self._b(key).download_blob().readall()
        except ResourceNotFoundError: raise NotFound(key)

    def put_bytes(self, key, data, content_type=None, if_match=None, if_none_match=False):
        """Returns the new etag."""
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
        from azure.storage.blob import ContentSettings
        kw = dict(overwrite=not if_none_match, content_settings=ContentSettings(content_type=content_type or _content_type(key)))
        if if_match is not None: kw.update(etag=if_match, match_condition=MatchConditions.IfNotModified)
        try: return self._b(key).upload_blob(data, **kw)['etag']
        except (ResourceExistsError, ResourceModifiedError, ResourceNotFoundError): raise Conflict(key)

    def get_json(self, key):
        """(object, etag)."""
        from azure.core.exceptions import ResourceNotFoundError
        try: d = self._b(key).download_blob()
        except ResourceNotFoundError: raise NotFound(key)
        return json.loads(d.readall()), d.properties.etag

    def put_json(self, key, obj, if_match=None, if_none_match=False):
        return self.put_bytes(key, json.dumps(obj, indent=1, default=str).encode(), 'application/json', if_match, if_none_match)

    def exists(self, key):
        return self._b(key).exists()

    def list(self, prefix):
        """Every key under prefix, sorted."""
        n = len(self.prefix)
        return sorted(name[n:] for name in self.container.list_blob_names(name_starts_with=self.prefix + prefix))

    def children(self, prefix):
        """The names one level below prefix ('captures/' -> capture ids and index.json), sorted."""
        p = self.prefix + (prefix.rstrip('/') + '/' if prefix.strip('/') else '')
        return sorted(item.name[len(p):].rstrip('/') for item in self.container.walk_blobs(name_starts_with=p, delimiter='/'))

    def delete(self, key):
        from azure.core.exceptions import ResourceNotFoundError
        try: self._b(key).delete_blob()
        except ResourceNotFoundError: pass

    def delete_prefix(self, prefix):
        for k in self.list(prefix): self.delete(k)


def _content_type(key):
    return mimetypes.guess_type(key)[0] or {'.heic': 'image/heic'}.get(os.path.splitext(key)[1].lower(), 'application/octet-stream')


def open_store(local_root=None):
    """Azure Blob Storage when AZURE_STORAGE_CONNECTION_STRING is set (container SHELFSCAN_CONTAINER, default 'shelfscan'),
    else a folder: local_root, or ./var/store next to the app."""
    if os.environ.get('AZURE_STORAGE_CONNECTION_STRING'):
        return BlobStore(os.environ['AZURE_STORAGE_CONNECTION_STRING'], os.environ.get('SHELFSCAN_CONTAINER') or 'shelfscan')
    return LocalStore(local_root or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'var', 'store'))
