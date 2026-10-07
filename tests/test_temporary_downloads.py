import concurrent.futures
import json
import os
from pathlib import Path

import pytest

from src import temporary_downloads as downloads


@pytest.fixture
def clock():
    return [1_700_000_000.0]


@pytest.fixture
def store(tmp_path, clock):
    instance = downloads.TemporaryDownloadStore(tmp_path / 'outputs', clock=lambda: clock[0])
    yield instance
    instance.close()


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'validated.epub'
    path.write_bytes(b'validated EPUB bytes')
    return path


def read(store, token):
    opened = store.open(token)
    with opened.stream:
        return opened.stream.read()


def test_publish_exact_bytes_permissions_metadata_and_independent_names(store, source):
    published = store.publish(source, 'book.epub')
    assert downloads.TOKEN_PATTERN.fullmatch(published.token)
    assert len(published.token) == 43
    assert published.file_name == 'book-repaired.epub'
    assert published.expires_in_seconds == 900
    assert read(store, published.token) == source.read_bytes()
    assert read(store, published.token) == source.read_bytes()
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert (store.root / (published.token + '.epub')).stat().st_mode & 0o777 == 0o600
    metadata = json.loads((store.root / (published.token + '.json')).read_text())
    assert metadata['expires'] - metadata['created'] == 900
    assert not any('book' in entry.name or entry.name.startswith('.pending') for entry in store.root.iterdir())


@pytest.mark.parametrize('name', [None, '', '../../secret.epub', '..\\secret.epub', '/etc/passwd',
    'C:\\something\\file.epub', 'hello\x00.epub', 'a\r\n.epub', 'a\x7f.epub', 'کتاب.epub',
    'a' * 121 + '.epub', 'book', '...', 'a/b.epub', 'a\\b.epub', 'a..b.epub', '.epub', '"x.epub'])
def test_untrusted_names_fall_back_and_never_determine_paths(store, source, name):
    published = store.publish(source, name)
    assert published.file_name == 'repaired.epub'
    assert {entry.name for entry in store.root.iterdir()} == {
        '.lock', published.token + '.epub', published.token + '.json'}


@pytest.mark.parametrize('name,expected', [('Book.EPUB', 'Book-repaired.epub'),
    ('my book-1.epub', 'my book-1-repaired.epub'), ('book.epub', 'book-repaired.epub')])
def test_safe_display_names(name, expected):
    assert downloads.repaired_filename(name) == expected


@pytest.mark.parametrize('token', ['', '../secret', 'a' * 42, 'a' * 44, 'a' * 42 + '.',
    'a' * 42 + '/', 'a' * 42 + '\\', 'a' * 42 + '\x00', 'é' * 43, 'Z' * 43])
def test_unknown_and_malformed_tokens_are_uniform(store, token):
    with pytest.raises(downloads.DownloadStoreError, match='^download_unavailable$'):
        store.open(token)


def test_expiry_rejects_removes_and_multiple_downloads_work(store, source, clock):
    item = store.publish(source)
    clock[0] += 899
    assert read(store, item.token) == source.read_bytes()
    clock[0] += 1
    with pytest.raises(downloads.DownloadStoreError):
        store.open(item.token)
    assert set(entry.name for entry in store.root.iterdir()) == {'.lock'}


def test_restart_sweeps_expired_orphans_and_partial_publications(tmp_path, source, clock):
    root = tmp_path / 'outputs'
    first = downloads.TemporaryDownloadStore(root, clock=lambda: clock[0])
    item = first.publish(source)
    first.close()
    (root / ('.pending-' + 'A' * 43)).write_bytes(b'partial')
    (root / ('B' * 43 + '.epub')).write_bytes(b'orphan')
    clock[0] += 900
    second = downloads.TemporaryDownloadStore(root, clock=lambda: clock[0])
    try:
        assert set(entry.name for entry in root.iterdir()) == {'.lock'}
        with pytest.raises(downloads.DownloadStoreError):
            second.open(item.token)
    finally:
        second.close()


def test_unexpired_object_survives_restart(tmp_path, source, clock):
    root = tmp_path / 'outputs'
    first = downloads.TemporaryDownloadStore(root, clock=lambda: clock[0])
    item = first.publish(source)
    first.close()
    second = downloads.TemporaryDownloadStore(root, clock=lambda: clock[0])
    try:
        assert read(second, item.token) == source.read_bytes()
    finally:
        second.close()


@pytest.mark.parametrize('corruption', ['broken_json', 'oversized_json', 'missing_epub', 'wrong_size',
    'symlink_epub', 'symlink_json', 'nan', 'huge_int', 'unsafe_name'])
def test_corrupt_metadata_and_links_never_serve_and_sweep_safely(store, source, corruption):
    item = store.publish(source)
    meta = store.root / (item.token + '.json')
    output = store.root / (item.token + '.epub')
    data = json.loads(meta.read_text())
    if corruption == 'broken_json':
        meta.write_text('{')
    elif corruption == 'oversized_json':
        meta.write_text(' ' * 1025)
    elif corruption == 'missing_epub':
        output.unlink()
    elif corruption == 'wrong_size':
        output.write_bytes(b'wrong')
    elif corruption.startswith('symlink'):
        target = meta if corruption == 'symlink_json' else output
        target.unlink()
        target.symlink_to(source)
    else:
        data['created' if corruption in ('nan', 'huge_int') else 'file_name'] = {
            'nan': float('nan'), 'huge_int': 10**200, 'unsafe_name': 'x"\r\n.epub'}[corruption]
        meta.write_text(json.dumps(data))
    with pytest.raises(downloads.DownloadStoreError):
        store.open(item.token)
    store.cleanup()
    assert source.read_bytes() == b'validated EPUB bytes'
    assert set(entry.name for entry in store.root.iterdir()) == {'.lock'}


def test_cleanup_does_not_follow_links_or_touch_unrelated_files(store, tmp_path, clock):
    outside = tmp_path / 'keep'
    outside.write_bytes(b'private')
    (store.root / ('.pending-' + 'A' * 43)).symlink_to(outside)
    (store.root / 'operator-note').write_bytes(b'keep')
    store.cleanup()
    assert outside.read_bytes() == b'private'
    assert (store.root / 'operator-note').read_bytes() == b'keep'


def test_single_process_owner_and_unsafe_root_rejected(store, tmp_path):
    with pytest.raises(downloads.DownloadStoreError, match='storage_unavailable'):
        downloads.TemporaryDownloadStore(store.root)
    link = tmp_path / 'link'
    link.symlink_to(store.root)
    with pytest.raises(downloads.DownloadStoreError):
        downloads.TemporaryDownloadStore(link)
    public = tmp_path / 'public'
    public.mkdir(mode=0o755)
    with pytest.raises(downloads.DownloadStoreError):
        downloads.TemporaryDownloadStore(public)


def test_output_limit_and_quota(store, source, monkeypatch):
    monkeypatch.setattr(downloads, 'MAX_OUTPUT_BYTES', 10)
    with pytest.raises(downloads.DownloadStoreError, match='output_too_large'):
        store.publish(source)
    assert set(entry.name for entry in store.root.iterdir()) == {'.lock'}
    monkeypatch.setattr(downloads, 'MAX_OUTPUT_BYTES', 100)
    monkeypatch.setattr(downloads, 'MAX_STORED_BYTES', len(source.read_bytes()))
    store.publish(source)
    with pytest.raises(downloads.DownloadStoreError, match='storage_capacity_reached'):
        store.publish(source)


def test_file_count_quota(store, source, monkeypatch):
    monkeypatch.setattr(downloads, 'MAX_STORED_FILES', 1)
    store.publish(source)
    with pytest.raises(downloads.DownloadStoreError, match='storage_capacity_reached'):
        store.publish(source)


@pytest.mark.parametrize('kind', ['symlink', 'directory', 'missing', 'empty'])
def test_no_nonregular_missing_or_empty_publication(store, source, tmp_path, kind):
    target = tmp_path / 'bad.epub'
    if kind == 'symlink':
        target.symlink_to(source)
    elif kind == 'directory':
        target.mkdir()
    elif kind == 'empty':
        target.touch()
    with pytest.raises(downloads.DownloadStoreError):
        store.publish(target)
    assert set(entry.name for entry in store.root.iterdir()) == {'.lock'}


@pytest.mark.parametrize('point', ['copy', 'metadata', 'fsync', 'permission'])
def test_atomic_failure_rolls_back_without_secret_error(store, source, monkeypatch, point):
    real_link = os.link
    links = []
    def fail_link(src, dst, **kwargs):
        links.append(dst)
        if point == 'copy' or (point == 'metadata' and dst.endswith('.json')):
            raise OSError('disk full SECRET')
        return real_link(src, dst, **kwargs)
    monkeypatch.setattr(downloads.os, 'link', fail_link)
    if point == 'fsync':
        monkeypatch.setattr(downloads.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('SECRET')))
    if point == 'permission':
        real_open = os.open
        def fail_open(path, *args, **kwargs):
            if str(path).startswith('.pending-'):
                raise PermissionError('SECRET')
            return real_open(path, *args, **kwargs)
        monkeypatch.setattr(downloads.os, 'open', fail_open)
    with pytest.raises(downloads.DownloadStoreError, match='^publication_failed$'):
        store.publish(source)
    assert set(entry.name for entry in store.root.iterdir()) == {'.lock'}


def test_token_collision_never_overwrites_existing_object(store, source, monkeypatch):
    first = store.publish(source)
    monkeypatch.setattr(downloads.secrets, 'token_urlsafe', lambda n: first.token)
    with pytest.raises(downloads.DownloadStoreError, match='token_collision'):
        store.publish(source)
    assert read(store, first.token) == source.read_bytes()


def test_concurrent_publications_and_downloads_keep_objects_distinct(store, tmp_path):
    sources = []
    for i in range(12):
        path = tmp_path / f'{i}.epub'
        path.write_bytes(bytes([i]) * 5000)
        sources.append(path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        published = list(pool.map(store.publish, sources))
        assert len({item.token for item in published}) == 12
        received = list(pool.map(lambda item: read(store, item.token), published))
    assert received == [path.read_bytes() for path in sources]
    # A token always selects its own object; this is bearer access, not user auth.
    assert read(store, published[0].token) != read(store, published[1].token)


def test_open_descriptor_finishes_safely_after_expiry_unlink(store, source, clock):
    item = store.publish(source)
    first, second = store.open(item.token), store.open(item.token)
    clock[0] += 900
    store.cleanup()
    assert not (store.root / (item.token + '.epub')).exists()
    with first.stream, second.stream:
        assert first.stream.read() == second.stream.read() == source.read_bytes()
    with pytest.raises(downloads.DownloadStoreError):
        store.open(item.token)


@pytest.mark.parametrize('origin', ['http://example.org', 'https://localhost', 'https://127.0.0.1',
    'https://[::1]', 'https://x.localhost', 'https://example.org:8000', 'https://u:p@example.org',
    'https://example.org/path', 'https://example.org?q', 'https://example.org#x',
    'https://example.org\n', 'https://example.org\\evil', 'https://-evil.example.org', 'https://é.example.org'])
def test_bad_public_origins(origin):
    with pytest.raises(ValueError):
        downloads.public_origin(origin)


def test_configured_origin_canonicalization():
    assert downloads.public_origin('https://DEV.example.org:443/') == 'https://dev.example.org'
