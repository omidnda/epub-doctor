import struct
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from src.diagnoser import diagnose_epub
from src.validator import validate_epub
from test_pkg005 import replace_central_extra
from src.zip_inspection import inspect_local_headers

EXPECTED = ['container_xml_missing']
OPF = b'<package xmlns="http://www.idpf.org/2007/opf" version="3.0"/>'
FIXTURES = Path(__file__).parent / 'fixtures' / 'rsc002'


def report(ids=('RSC-002',), publication=None):
    return {'messages': [{'ID': code} for code in ids], 'publication': publication or {}}


@pytest.fixture
def build(tmp_path):
    def make(files=None, mimetype=b'application/epub+zip', first=True,
             compression=0, extra=b''):
        path = tmp_path / 'test.epub'
        with ZipFile(path, 'w') as archive:
            def write_mimetype():
                if mimetype is not None:
                    entry = ZipInfo('mimetype')
                    entry.compress_type = compression
                    entry.extra = extra
                    archive.writestr(entry, mimetype)
            if first:
                write_mimetype()
            for name, data in (files if files is not None else {'OPS/package.opf': OPF}).items():
                archive.writestr(name, data)
            if not first:
                write_mimetype()
        return path
    return make


@pytest.mark.parametrize('filename,expected', [('epub2-missing-container.epub', EXPECTED),
    ('epub3-missing-container.epub', EXPECTED), ('not-ocf.epub', [])])
def test_official_fixtures_real_epubcheck(filename, expected):
    path = FIXTURES / filename
    original = path.read_bytes()
    result = validate_epub(str(path))
    assert 'RSC-002' in {message['ID'] for message in result['messages']}
    diagnoses = diagnose_epub(str(path), result)
    assert (EXPECTED[0] in diagnoses) == bool(expected)
    if expected:
        assert diagnoses == expected
    assert path.read_bytes() == original


@pytest.mark.parametrize('version', ['2.0', '3.0', '3.3', ''])
def test_one_real_opf_without_version_gate(build, version):
    xml = OPF.replace(b'3.0', version.encode())
    assert diagnose_epub(str(build({'OPS/package.opf': xml})), report()) == EXPECTED


@pytest.mark.parametrize('files', [{}, {'word/document.xml': b'<document/>'},
    {'OPS/package.opf': b'<broken>'}, {'OPS/package.opf': b'<html/>'},
    {'OPS/package.opf': b'<package xmlns="wrong"/>'},
    {'OPS/package.opf': OPF, 'other.opf': OPF},
    {'OPS/package.opf': b'<!DOCTYPE package>' + OPF},
    {'OPS/package.opf': OPF + b' ' * (8 * 1024 * 1024)},
    {'OPS/package.opf': OPF, 'META-INF/container.xml': b'<container/>'},
    {'OPS/package.xml': OPF},
    {'OPS/package.opf': OPF, 'other.opf': b'<!DOCTYPE package>' + OPF},
    {'OPS/package.opf': OPF, 'other.opf': OPF + b' ' * (8 * 1024 * 1024)},
])
def test_no_safe_unique_package(build, files):
    assert diagnose_epub(str(build(files)), report()) == []


@pytest.mark.parametrize('invalid', [b'<broken>', b'<html/>', b'<package xmlns="wrong"/>'])
def test_invalid_candidate_ignored_when_one_real_package_exists(build, invalid):
    assert diagnose_epub(str(build({'OPS/package.opf': OPF, 'other.opf': invalid})), report()) == EXPECTED


@pytest.mark.parametrize('path', ['/OPS/package.opf', '../package.opf', 'OPS/../package.opf',
    './package.opf', 'OPS//package.opf', 'OPS\\package.opf', 'C:/package.opf',
    'OPS/%70ackage.opf', 'OPS/package.opf?x', 'OPS/package.opf#x',
    'OPS/\x01package.opf', 'OPS/\x85package.opf', ' OPS/package.opf', 'OPS/package.opf/', '//host/package.opf'])
def test_suspicious_package_paths(build, path):
    assert diagnose_epub(str(build({path: OPF})), report()) == []


def test_unsafe_candidate_prevents_uniqueness_claim(build):
    assert diagnose_epub(str(build({'good.opf': OPF, '../other.opf': OPF})), report()) == []


@pytest.mark.parametrize('name', ['OPS/package.opf', 'mimetype', 'META-INF/container.xml', 'unrelated'])
def test_duplicate_names(build, name):
    path = build({'OPS/package.opf': OPF, **({name: b''} if name not in ('OPS/package.opf', 'mimetype') else {})})
    with pytest.warns(UserWarning), ZipFile(path, 'a') as archive:
        archive.writestr(name, b'duplicate')
    assert diagnose_epub(str(path), report()) == []


@pytest.mark.parametrize('kwargs', [{'mimetype': None}, {'first': False}, {'mimetype': b'wrong'},
    {'compression': ZIP_DEFLATED}, {'extra': struct.pack('<HH', 0xbeef, 0)}])
def test_invalid_mimetype(build, kwargs):
    assert diagnose_epub(str(build(**kwargs)), report()) == []


@pytest.mark.parametrize('location', ['local', 'central'])
def test_mimetype_extra_on_only_one_header(build, location):
    extra = struct.pack('<HH', 0xbeef, 0)
    path = build(extra=extra if location == 'local' else b'')
    replace_central_extra(path, 'mimetype', b'' if location == 'local' else extra)
    with ZipFile(path) as archive:
        # Independently confirm these are consistent ZIPs with one-sided extras.
        local_extra = inspect_local_headers(archive)['mimetype']
        assert bool(local_extra) == (location == 'local')
        assert bool(archive.getinfo('mimetype').extra) == (location == 'central')
        assert archive.read('mimetype') == b'application/epub+zip'
    assert diagnose_epub(str(path), report()) == []


@pytest.mark.parametrize('flag', ['hasSignatures', 'hasEncryption'])
def test_publication_protection(build, flag):
    assert diagnose_epub(str(build()), report(publication={flag: True})) == []


@pytest.mark.parametrize('name', ['META-INF/signatures.xml', 'META-INF/encryption.xml'])
def test_archive_protection(build, name):
    assert diagnose_epub(str(build({'OPS/package.opf': OPF, name: b''})), report()) == []


@pytest.mark.parametrize('name', ['OPS/package.opf', 'mimetype', 'unrelated.bin'])
def test_encrypted_entry(build, name):
    path = build({'OPS/package.opf': OPF, 'unrelated.bin': b'data'})
    with ZipFile(path) as archive:
        local_offset = archive.getinfo(name).header_offset
    data = bytearray(path.read_bytes())
    struct.pack_into('<H', data, local_offset + 6, 1)
    offset = data.index(b'PK\x01\x02')
    while True:
        name_len, extra_len, comment_len = struct.unpack_from('<HHH', data, offset + 28)
        if data[offset + 46:offset + 46 + name_len].decode() == name:
            struct.pack_into('<H', data, offset + 8, 1)
            break
        offset += 46 + name_len + extra_len + comment_len
    path.write_bytes(data)
    assert diagnose_epub(str(path), report()) == []


@pytest.mark.parametrize('ids', [(), ('RSC-003',), ('OTHER',)])
def test_rsc002_required(build, ids):
    assert diagnose_epub(str(build()), report(ids)) == []


@pytest.mark.parametrize('mode,ids,expected', [
    ('missing', ('RSC-002', 'PKG-006'), ['mimetype_missing']),
    ('not_first', ('RSC-002', 'PKG-006'), ['mimetype_not_first']),
    ('incorrect', ('RSC-002', 'PKG-007'), ['mimetype_incorrect_content']),
    ('extra', ('RSC-002', 'PKG-005'), ['mimetype_has_extra_field'])])
def test_existing_pkg_diagnoses_preserved(make_epub, mode, ids, expected):
    source = make_epub(mode)
    with ZipFile(source) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()
                   if entry.filename != 'META-INF/container.xml']
    with ZipFile(source, 'w') as archive:
        for entry, data in entries:
            archive.writestr(entry, data)
    assert diagnose_epub(str(source), report(ids)) == expected


def test_bad_zip_and_missing_input(tmp_path):
    path = tmp_path / 'bad.epub'
    assert diagnose_epub(str(path), report()) == []
    path.write_bytes(b'not zip')
    assert diagnose_epub(str(path), report()) == []


def test_bad_crc_refused(build):
    path = build()
    with ZipFile(path) as archive:
        offset = archive.getinfo('OPS/package.opf').header_offset
    data = bytearray(path.read_bytes())
    name_size, extra_size = struct.unpack_from('<HH', data, offset + 26)
    data[offset + 30 + name_size + extra_size] ^= 1
    path.write_bytes(data)
    assert diagnose_epub(str(path), report()) == []


def test_discovery_is_bounded_and_does_not_read_arbitrary_payloads(build, monkeypatch):
    path = build({'OPS/package.opf': OPF, 'huge.bin': b'arbitrary binary data'})
    original = ZipFile.open
    def checked_open(archive, name, *args, **kwargs):
        assert getattr(name, 'filename', name) != 'huge.bin'
        return original(archive, name, *args, **kwargs)
    monkeypatch.setattr(ZipFile, 'open', checked_open)
    assert diagnose_epub(str(path), report()) == EXPECTED


def test_candidate_count_bound(build):
    assert diagnose_epub(str(build({f'{index}.opf': b'<other/>' for index in range(65)})), report()) == []


def test_aggregate_size_bound(build):
    files = {'good.opf': OPF, **{f'{index}.opf': b'<other/>' + b' ' * (8 * 1024 * 1024 - 8) for index in range(4)}}
    assert diagnose_epub(str(build(files)), report()) == []


def test_uppercase_extension(build):
    assert diagnose_epub(str(build({'OPS/package.OPF': OPF})), report()) == EXPECTED


@pytest.mark.parametrize('code', ['PKG-005', 'PKG-006', 'PKG-007'])
def test_reported_mimetype_problem_blocks_subset_even_when_not_confirmed(build, code):
    assert diagnose_epub(str(build()), report(('RSC-002', code))) == []


def test_zip64_refused(build):
    path = build()
    with ZipFile(path, 'a') as archive:
        with archive.open(ZipInfo('unrelated'), 'w', force_zip64=True) as stream:
            stream.write(b'payload')
    assert diagnose_epub(str(path), report()) == []


def test_local_header_conflict_refused(build):
    path = build()
    data = bytearray(path.read_bytes())
    struct.pack_into('<H', data, 8, ZIP_DEFLATED)
    path.write_bytes(data)
    assert diagnose_epub(str(path), report()) == []
