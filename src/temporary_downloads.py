"""Private, bounded Linux filesystem store for short-lived bearer downloads.

One process owns a root (flock); threads share a lock. Metadata is the atomic
publication marker. No user path/name/reference is used as a storage key.
"""

import fcntl
import json
import logging
import math
import os
import re
import secrets
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

DOWNLOAD_TTL_SECONDS = 900
CLEANUP_INTERVAL_SECONDS = 30
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_STORED_BYTES = 200 * 1024 * 1024
MAX_STORED_FILES = 100
MAX_DOWNLOAD_SECONDS = 60
TOKEN_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}')
DEFAULT_STORAGE_ROOT = Path(f'/tmp/epub-doctor-downloads-{os.getuid()}')


class DownloadStoreError(Exception):
    """Expected failure, carrying only a public, non-secret reason code."""


def public_origin(value: str) -> str:
    """Accept an explicit HTTPS DNS origin, never request/forwarded headers."""
    try:
        value.encode('ascii')
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        labels = host.split('.')
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.path not in ('', '/')
                or '?' in value or '#' in value or '\\' in value or len(value) > 1024
                or len(labels) < 2 or len(host) > 253
                or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
                or labels[-1] in ('localhost', 'local', 'internal')
                or re.fullmatch(r'[0-9.]+', host)
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError('public origin must be an HTTPS DNS origin on port 443, without credentials, path or query') from None
    return 'https://' + host


def repaired_filename(value: str | None) -> str:
    """Conservative ASCII display name; never a physical storage filename."""
    if (not isinstance(value, str) or len(value) > 120
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]*\.epub', value, re.IGNORECASE)):
        return 'repaired.epub'
    stem = value[:-5].strip(' .')
    if not stem or '..' in stem:
        return 'repaired.epub'
    return stem + '-repaired.epub'


@dataclass(frozen=True)
class PublishedDownload:
    token: str
    file_name: str
    expires_in_seconds: int = DOWNLOAD_TTL_SECONDS


@dataclass
class OpenDownload:
    stream: BinaryIO
    file_name: str
    size: int


class TemporaryDownloadStore:
    def __init__(self, root: Path = DEFAULT_STORAGE_ROOT, *, clock=time.time):
        self.root = Path(root)
        self.clock = clock
        self._lock = threading.RLock()
        self._root_fd = None
        self._owner_fd = None
        try:
            self.root.mkdir(mode=0o700, exist_ok=True)
            self._root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(self._root_fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise DownloadStoreError('storage_root_not_private')
            self._owner_fd = os.open('.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=self._root_fd)
            if not stat.S_ISREG(os.fstat(self._owner_fd).st_mode):
                raise DownloadStoreError('storage_lock_invalid')
            fcntl.flock(self._owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.cleanup()
        except (OSError, DownloadStoreError):
            self.close()
            raise DownloadStoreError('storage_unavailable') from None

    def close(self):
        with self._lock:
            for attr in ('_owner_fd', '_root_fd'):
                fd = getattr(self, attr)
                if fd is not None:
                    os.close(fd)
                    setattr(self, attr, None)

    def _unlink(self, name):
        try:
            os.unlink(name, dir_fd=self._root_fd)
        except FileNotFoundError:
            pass

    def _remove(self, token):
        # Delete the publication marker first; existing open descriptors may finish.
        self._unlink(token + '.json')
        self._unlink(token + '.epub')

    def _metadata(self, token):
        fd = os.open(token + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._root_fd)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
                raise ValueError('invalid metadata')
            data = json.loads(stream.read(1025))
        if not isinstance(data, dict) or set(data) != {'created', 'expires', 'size', 'file_name'}:
            raise ValueError('invalid metadata')
        created, expires, size, name = (data[key] for key in ('created', 'expires', 'size', 'file_name'))
        if (type(created) not in (float, int) or type(expires) not in (float, int)
                or not 0 <= created < 1e12 or not 0 <= expires < 1e12
                or not math.isfinite(created) or not math.isfinite(expires)
                or expires - created != DOWNLOAD_TTL_SECONDS or created > self.clock()
                or type(size) is not int or not 0 < size <= MAX_OUTPUT_BYTES
                or not isinstance(name, str) or len(name) > 140
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]*\.epub', name)):
            raise ValueError('invalid metadata')
        return data

    def cleanup(self):
        """Sweep expired/corrupt/orphaned objects, including crash leftovers."""
        with self._lock:
            for name in os.listdir(self._root_fd):
                if re.fullmatch(r'\.pending-[A-Za-z0-9_-]{43}', name):
                    self._unlink(name)
                elif re.fullmatch(r'[A-Za-z0-9_-]{43}\.(?:json|epub)', name):
                    token = name.split('.')[0]
                    try:
                        data = self._metadata(token)
                        info = os.stat(token + '.epub', dir_fd=self._root_fd, follow_symlinks=False)
                        if (data['expires'] <= self.clock() or not stat.S_ISREG(info.st_mode)
                                or info.st_size != data['size']):
                            self._remove(token)
                    except (OSError, ValueError, UnicodeError):
                        self._remove(token)

    def publish(self, source: Path, filename: str | None = None) -> PublishedDownload:
        """Copy only the caller's validated output, then atomically mark ready."""
        with self._lock:
            owned = []
            try:
                self.cleanup()
                sizes = [os.stat(name, dir_fd=self._root_fd, follow_symlinks=False).st_size
                         for name in os.listdir(self._root_fd)
                         if re.fullmatch(r'[A-Za-z0-9_-]{43}\.epub', name)]
                fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as input_file:
                    info = os.fstat(input_file.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                        raise DownloadStoreError('output_not_regular')
                    if info.st_size > MAX_OUTPUT_BYTES:
                        raise DownloadStoreError('output_too_large')
                    if len(sizes) >= MAX_STORED_FILES or sum(sizes) + info.st_size > MAX_STORED_BYTES:
                        raise DownloadStoreError('storage_capacity_reached')
                    for _ in range(10):
                        token = secrets.token_urlsafe(32)
                        if not TOKEN_PATTERN.fullmatch(token):
                            raise DownloadStoreError('token_generation_failed')
                        if not any(os.path.lexists(self.root / (token + suffix)) for suffix in ('.json', '.epub')):
                            break
                    else:
                        raise DownloadStoreError('token_collision')
                    pending = '.pending-' + token
                    fd = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=self._root_fd)
                    owned.append(pending)
                    with os.fdopen(fd, 'wb') as output:
                        total = 0
                        while chunk := input_file.read(64 * 1024):
                            total += len(chunk)
                            if total > MAX_OUTPUT_BYTES:
                                raise DownloadStoreError('output_too_large')
                            output.write(chunk)
                        if total != info.st_size:
                            raise DownloadStoreError('output_changed')
                        output.flush()
                        os.fsync(output.fileno())
                output_name = token + '.epub'
                os.link(pending, output_name, src_dir_fd=self._root_fd, dst_dir_fd=self._root_fd, follow_symlinks=False)
                owned.append(output_name)
                self._unlink(pending)
                now = self.clock()
                name = repaired_filename(filename)
                metadata = json.dumps(dict(created=now, expires=now + DOWNLOAD_TTL_SECONDS,
                                           size=total, file_name=name)).encode('ascii')
                fd = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=self._root_fd)
                with os.fdopen(fd, 'wb') as output:
                    output.write(metadata)
                    output.flush()
                    os.fsync(output.fileno())
                os.link(pending, token + '.json', src_dir_fd=self._root_fd, dst_dir_fd=self._root_fd, follow_symlinks=False)
                owned.append(token + '.json')
                self._unlink(pending)
                os.fsync(self._root_fd)
                # Check what the download endpoint will actually open before claiming delivery.
                downloaded = self.open(token)
                downloaded.stream.close()
                return PublishedDownload(token, name)
            except (OSError, DownloadStoreError) as exc:
                for name in reversed(owned):
                    try:
                        self._unlink(name)
                    except OSError:
                        logging.getLogger(__name__).warning('Incomplete publication cleanup failed; sweep will retry')
                raise DownloadStoreError(str(exc) if isinstance(exc, DownloadStoreError) else 'publication_failed') from None

    def open(self, token: str) -> OpenDownload:
        if not TOKEN_PATTERN.fullmatch(token):
            raise DownloadStoreError('download_unavailable')
        with self._lock:
            try:
                data = self._metadata(token)
                if data['expires'] <= self.clock():
                    self._remove(token)
                    raise DownloadStoreError('download_unavailable')
                fd = os.open(token + '.epub', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._root_fd)
                stream = os.fdopen(fd, 'rb')
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != data['size']:
                    stream.close()
                    raise DownloadStoreError('download_unavailable')
                return OpenDownload(stream, data['file_name'], data['size'])
            except (OSError, ValueError, UnicodeError, DownloadStoreError):
                raise DownloadStoreError('download_unavailable') from None
