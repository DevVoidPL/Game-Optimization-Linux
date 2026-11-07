"""Official Arch package download only; never execute or install the payload."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import threading
import stat
import tempfile
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit


API = 'https://repo.seafoam-labs.org/api/v1/packages/x86_64/aqueous-core'
MAX_SIZE = 256 * 1024 * 1024


class DownloadError(ValueError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def host_platform():
    paths = ('/run/host/os-release', '/run/host/etc/os-release', '/run/host/usr/lib/os-release') if os.environ.get('FLATPAK_ID') else ('/etc/os-release', '/usr/lib/os-release')
    for name in paths:
        try:
            values = dict(line.split('=', 1) for line in Path(name).read_text().splitlines()
                          if '=' in line and not line.lstrip().startswith('#'))
        except OSError:
            continue
        ids = ' '.join(values.get(key, '').strip().strip('\"\'') for key in ('ID', 'ID_LIKE')).split()
        return ('arch' if 'arch' in ids else 'unknown'), platform.machine()
    raise DownloadError('discovery', 'Cannot read host os-release. Flatpak must expose /run/host/os-release; sandbox runtime is not the host distribution.')


def download(directory, *, distribution, architecture, opener=urllib.request.urlopen, progress=lambda *_: None):
    if distribution != 'arch' or architecture != 'x86_64':
        raise DownloadError('unsupported_platform', f'Unsupported host: {distribution} {architecture}. Only the official Arch x86_64 package is supported; nothing was installed.')
    def fetch(url):
        try:
            response = opener(url, timeout=30)
        except HTTPError as error:
            raise DownloadError('http', f'HTTP {error.code} fetching {url}') from error
        except (URLError, TimeoutError) as error:
            raise DownloadError('network', f'Network failure fetching {url}: {error}') from error
        final = response.geturl() if hasattr(response, 'geturl') else url
        if urlsplit(final).scheme != 'https' or urlsplit(final).netloc != urlsplit(url).netloc:
            response.close()
            raise DownloadError('discovery', 'Download redirected outside the verified official origin')
        return response
    with fetch(API) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise DownloadError('discovery', 'Official package metadata is too large')
    try:
        metadata = json.loads(data)
        if not isinstance(metadata, dict): raise ValueError('Expected an object')
    except ValueError as error:
        raise DownloadError('discovery', 'Official package discovery returned invalid JSON metadata') from error
    name, url, digest = metadata.get('fileName', ''), metadata.get('downloadUrl', ''), metadata.get('sha256Sum', '')
    if (metadata.get('name') != 'aqueous-core' or metadata.get('architecture') != architecture or
            not re.fullmatch(r'aqueous-core-[A-Za-z0-9.+_:-]+-x86_64\.pkg\.tar\.zst', name) or
            not re.fullmatch(r'[a-fA-F0-9]{64}', digest) or
            url != 'https://mirrors.seafoam-labs.org/seafoam-labs/x86_64/' + name):
        raise DownloadError('discovery', 'Official package metadata failed filename, origin, architecture or SHA-256 validation')
    total = metadata.get('compressedSize')
    if not isinstance(total, int) or isinstance(total, bool) or not 0 < total <= MAX_SIZE:
        raise DownloadError('discovery', 'Invalid official package size')
    progress(0, total)
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    target = directory/name
    # Lock the directory inode: no lock-file symlink or stale lock-file cleanup.
    lock = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(lock, fcntl.LOCK_EX)
    temporary = None
    received, checksum = 0, hashlib.sha256()
    def existing():
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return False
        except OSError as error:
            raise DownloadError('existing_file', 'Existing download is unsafe; left untouched: ' + str(target)) from error
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != total:
                raise DownloadError('existing_file', 'Existing package has invalid type/size; move it aside manually: ' + str(target))
            check = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = os.fstat(stream.fileno())
            current = target.lstat()
            if (check.lower() != digest.lower() or
                    (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or
                    (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino)):
                raise DownloadError('existing_file', 'Existing package failed SHA-256/identity verification; left untouched: ' + str(target))
        progress(total, total)
        return True
    try:
        if existing():
            return target
        fd, name_part = tempfile.mkstemp(prefix=name + '.', suffix='.part', dir=directory)
        temporary = Path(name_part)
        stream = os.fdopen(fd, 'wb')
        with stream, fetch(url) as response:
            while True:
                block = response.read(128 * 1024)
                if not block: break
                received += len(block)
                if received > total: raise DownloadError('checksum', 'Downloaded package exceeds verified size')
                stream.write(block); checksum.update(block); progress(received, total)
            stream.flush(); os.fsync(stream.fileno())
        if received != total or checksum.hexdigest().lower() != digest.lower():
            raise DownloadError('checksum', 'Downloaded Aqueous package failed size/SHA-256 verification; file rejected')
        # Do not overwrite an existing download, including symlinks.
        try:
            os.link(temporary, target)
        except FileExistsError:
            if not existing():
                raise DownloadError('existing_file', 'Existing target changed during finalization; retry verification')
        os.fsync(lock)
        # Sidecar is optional and must never overwrite a user file/symlink.
        try:
            with target.with_suffix(target.suffix+'.sha256').open('x') as sidecar:
                sidecar.write(checksum.hexdigest()+'  '+target.name+'\n')
        except FileExistsError:
            pass
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        os.close(lock)


class DownloadJob:
    def __init__(self):
        self.lock = threading.Lock()
        self.value = {'state': 'idle', 'received': 0, 'total': 0}

    def status(self):
        with self.lock: return dict(self.value)

    def start(self, directory):
        with self.lock:
            if self.value['state'] == 'downloading': return False
            self.value = {'state': 'downloading', 'phase': 'discovery', 'received': 0, 'total': 0}
        def update(**values):
            with self.lock: self.value.update(values)
        def run():
            try:
                dist, arch = host_platform()
                path = download(directory, distribution=dist, architecture=arch,
                                progress=lambda received,total: update(phase='download', received=received,total=total))
                update(state='complete', phase='verified', path=str(path), error='',
                       message='Verified / already downloaded: size and SHA-256 match current official HTTPS metadata. PGP trust and dependency review remain separate. Nothing was installed.')
            except Exception as error:
                update(state='failed', error=str(error), errorCategory=getattr(error, 'category', 'io'))
        threading.Thread(target=run, name='aqueous-download', daemon=True).start()
        return True