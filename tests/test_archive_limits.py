from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from src import diagnoser, repairer, zip_inspection
from src.zip_inspection import UnsafeZipError, check_archive_limits


@pytest.mark.parametrize('sizes,reason', [
    ([64 * 1024 * 1024], None),
    ([64 * 1024 * 1024 + 1], 'archive_entry_too_large'),
    ([64 * 1024 * 1024] * 4, None),
    ([64 * 1024 * 1024] * 4 + [1], 'archive_total_too_large'),
])
def test_declared_uncompressed_size_boundaries(sizes, reason):
    entries = []
    for index, size in enumerate(sizes):
        entry = ZipInfo(str(index))
        entry.file_size = size
        entries.append(entry)
    archive = SimpleNamespace(infolist=lambda: entries)
    if reason:
        with pytest.raises(UnsafeZipError) as caught:
            check_archive_limits(archive)
        assert caught.value.reason == reason
    else:
        check_archive_limits(archive)


@pytest.mark.parametrize('kind', ['entry', 'total'])
def test_compressed_archive_refused_before_content_reads_or_mutation(monkeypatch, tmp_path, kind):
    source = tmp_path / 'bomb.epub'
    with ZipFile(source, 'w', compression=ZIP_DEFLATED) as archive:
        # Write a real high-ratio archive without allocating an entry-sized buffer.
        with archive.open('huge.xhtml', 'w') as target:
            for _ in range(65):
                target.write(b'\0' * (1024 * 1024))
        if kind == 'total':
            archive.writestr('second.xhtml', b'tiny')
    assert source.stat().st_size < 20 * 1024 * 1024
    if kind == 'total':
        monkeypatch.setattr(zip_inspection, 'MAX_UNCOMPRESSED_ENTRY_BYTES', 66 * 1024 * 1024)
        monkeypatch.setattr(zip_inspection, 'MAX_TOTAL_UNCOMPRESSED_BYTES', 65 * 1024 * 1024)
    before = source.read_bytes()
    validation = {'valid': False, 'messages': [{'ID': 'PKG-007'}], 'publication': {}}
    monkeypatch.setattr(repairer, 'validate_epub', lambda path: validation)
    monkeypatch.setattr(ZipFile, 'open', lambda *args, **kwargs: pytest.fail('Entry content was opened'))
    output = tmp_path / 'output.epub'
    result = repairer.repair_epub(str(source), str(output))
    reason = 'archive_entry_too_large' if kind == 'entry' else 'archive_total_too_large'
    assert result['reason'] == reason
    assert result['repaired'] == [] and result['output_file'] is None
    assert result['detected'] == []
    assert source.read_bytes() == before
    assert set(tmp_path.iterdir()) == {source}
    with pytest.raises(UnsafeZipError) as caught:
        diagnoser.diagnose_epub(str(source), validation)
    assert caught.value.reason == reason
