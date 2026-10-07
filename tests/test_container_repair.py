import struct
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

from src import diagnoser, repairer
from src.validator import validate_epub
from src.zip_inspection import UnsafeZipError
from test_rsc002 import build, FIXTURES, OPF

DIAGNOSIS = ['container_xml_missing']
CONTAINER = 'META-INF/container.xml'
OCF = '{urn:oasis:names:tc:opendocument:xmlns:container}'
METADATA = ('date_time', 'compress_type', 'comment', 'extra', 'internal_attr',
            'external_attr', 'create_system', 'create_version', 'extract_version')


def fake_validation(monkeypatch, ids=('RSC-002',), final_valid=True, publication=None):
    calls = []
    def validate(path):
        calls.append(path)
        return {'valid': final_valid if len(calls) > 1 else False,
                'messages': ([{'ID': code} for code in ids] if len(calls) == 1
                             else ([] if final_valid else [{'ID': 'OTHER'}])),
                'publication': publication or {}}
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    return calls


def check_xml(data, package_path):
    root = ElementTree.fromstring(data)
    assert root.tag == OCF + 'container' and root.get('version') == '1.0'
    groups = root.findall(OCF + 'rootfiles')
    assert len(groups) == 1
    files = groups[0].findall(OCF + 'rootfile')
    assert len(files) == 1 and len(list(root.iter(OCF + 'rootfile'))) == 1
    assert files[0].attrib == {'full-path': package_path, 'media-type': 'application/oebps-package+xml'}


def check_preservation(source, output, package_path):
    with ZipFile(source) as before, ZipFile(output) as after:
        names = before.namelist()
        assert after.namelist() == [names[0], CONTAINER, *names[1:]]
        assert after.comment == before.comment
        assert after.read('mimetype') == before.read('mimetype') == b'application/epub+zip'
        for entry in before.infolist():
            assert after.read(entry.filename) == before.read(entry)
            copied = after.getinfo(entry.filename)
            for field in METADATA:
                assert getattr(copied, field) == getattr(entry, field)
        check_xml(after.read(CONTAINER), package_path)
        new_entry = after.getinfo(CONTAINER)
        assert new_entry.date_time == (2020, 1, 1, 0, 0, 0)
        assert new_entry.compress_type == ZIP_STORED
        assert new_entry.extra == b''


@pytest.mark.parametrize('fixture,package_path', [('epub2-missing-container.epub', 'OPS/package.opf'),
    ('epub3-missing-container.epub', 'EPUB/package.opf')])
def test_official_real_repair(tmp_path, fixture, package_path):
    source = FIXTURES / fixture
    original = source.read_bytes()
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result == {'original_valid': False, 'detected': DIAGNOSIS, 'repaired': DIAGNOSIS,
                      'final_valid': True, 'output_file': str(output), 'remaining_messages': [], 'reason': None}
    assert validate_epub(str(output))['valid'] is True
    check_preservation(source, output, package_path)
    assert source.read_bytes() == original


def test_official_docx_no_container_repair(tmp_path):
    source = FIXTURES / 'not-ocf.epub'
    original = source.read_bytes()
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert 'container_xml_missing' not in result['detected']
    assert result['repaired'] == [] and result['output_file'] is None
    assert not output.exists() and list(tmp_path.iterdir()) == []
    assert source.read_bytes() == original


@pytest.mark.parametrize('path', ['OPS/package.opf', 'OPS/a"&<.opf', "OPS/a'.opf", 'OPS/کتاب.opf'])
def test_generation_escaping_and_determinism(path):
    data = repairer._build_container_xml(path)
    check_xml(data, path)
    assert data == repairer._build_container_xml(path)
    assert data.startswith(b'<?xml')


def test_escaped_exact_zip_path_and_shared_discovery(monkeypatch, build, tmp_path):
    package_path = 'OPS/a"&<.opf'
    source = build({package_path: OPF})
    calls = fake_validation(monkeypatch)
    discoveries = []
    original_helper = diagnoser._find_unique_package_path
    def find(archive, report):
        path = original_helper(archive, report)
        discoveries.append(path)
        return path
    monkeypatch.setattr(diagnoser, '_find_unique_package_path', find)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == result['repaired'] == DIAGNOSIS
    assert discoveries == [package_path, package_path]
    assert len(calls) == 2
    check_preservation(source, output, package_path)


def test_recovered_path_must_still_be_proven(monkeypatch, build, tmp_path):
    source = build()
    calls = fake_validation(monkeypatch)
    original_helper = diagnoser._find_unique_package_path
    discoveries = []
    def find(archive, report):
        discoveries.append(archive.filename)
        return original_helper(archive, report) if len(discoveries) == 1 else None
    monkeypatch.setattr(diagnoser, '_find_unique_package_path', find)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == DIAGNOSIS and result['repaired'] == []
    assert result['reason'] == 'unsafe_container_creation'
    assert len(calls) == 1 and not output.exists()
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize('files', [{}, {'a.opf': OPF, 'b.opf': OPF}, {'a.opf': b'<broken>'},
    {'a.opf': b'<html/>'}, {'a.opf': b'<package xmlns="wrong"/>'},
    {'../a.opf': OPF}, {'/a.opf': OPF}, {'a\\b.opf': OPF},
    {'a.opf': b'<!DOCTYPE package>' + OPF},
    {'a.opf': OPF + b' ' * (8 * 1024 * 1024)},
    {'a.opf': OPF, CONTAINER: b'<container/>'}])
def test_no_package_or_unsafe_package_refused(monkeypatch, build, tmp_path, files):
    source = build(files)
    original = source.read_bytes()
    calls = fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert 'container_xml_missing' not in result['detected']
    assert result['repaired'] == [] and result['output_file'] is None
    assert len(calls) == 1 and set(tmp_path.iterdir()) == {source}
    assert source.read_bytes() == original


@pytest.mark.parametrize('kwargs', [{'mimetype': None}, {'first': False}, {'mimetype': b'wrong'},
    {'compression': ZIP_DEFLATED}, {'extra': struct.pack('<HH', 0xbeef, 0)}])
def test_invalid_mimetype_no_container_repair(monkeypatch, build, tmp_path, kwargs):
    source = build(**kwargs)
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == result['repaired'] == []
    assert result['output_file'] is None and not output.exists()


@pytest.mark.parametrize('name', ['a.opf', CONTAINER, 'mimetype'])
def test_duplicate_entries_refused(monkeypatch, build, tmp_path, name):
    source = build({'a.opf': OPF, **({CONTAINER: b'<container/>'} if name == CONTAINER else {})})
    with ZipFile(source, 'a') as archive, pytest.warns(UserWarning):
        archive.writestr(name, b'duplicate')
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'duplicate_zip_entries'
    assert result['repaired'] == [] and not output.exists()


@pytest.mark.parametrize('protection', ['hasSignatures', 'hasEncryption', 'signatures', 'encryption', 'zip'])
def test_protection_refused(monkeypatch, build, tmp_path, protection):
    files = {'a.opf': OPF}
    if protection in ('signatures', 'encryption'):
        files[f'META-INF/{protection}.xml'] = b''
    source = build(files)
    if protection == 'zip':
        data = bytearray(source.read_bytes())
        offset = data.index(b'PK\x01\x02')
        struct.pack_into('<H', data, offset + 8, 1)
        struct.pack_into('<H', data, 6, 1)
        source.write_bytes(data)
    fake_validation(monkeypatch, publication={protection: True} if protection.startswith('has') else {})
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'signatures_or_encryption'
    assert result['repaired'] == [] and not output.exists()


@pytest.mark.parametrize('ids', [(), ('OTHER',), ('RSC-002', 'PKG-005'), ('RSC-002', 'PKG-006'), ('RSC-002', 'PKG-007')])
def test_message_guards(monkeypatch, build, tmp_path, ids):
    source = build()
    fake_validation(monkeypatch, ids)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == [] and result['repaired'] == []
    assert result['reason'] == 'no_supported_repair' and not output.exists()


def test_output_guards(monkeypatch, build, tmp_path):
    source = build()
    original = source.read_bytes()
    fake_validation(monkeypatch)
    assert repairer.repair_epub(str(source), str(source))['reason'] == 'output_is_input'
    output = tmp_path / 'out.epub'
    output.write_bytes(b'keep')
    fake_validation(monkeypatch)
    assert repairer.repair_epub(str(source), str(output))['reason'] == 'output_exists'
    assert output.read_bytes() == b'keep' and source.read_bytes() == original


def test_revalidation_failure(monkeypatch, build, tmp_path):
    source = build()
    original = source.read_bytes()
    calls = fake_validation(monkeypatch, final_valid=False)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert len(calls) == 2 and result['detected'] == DIAGNOSIS
    assert result['reason'] == 'revalidation_failed' and result['final_valid'] is False
    assert result['repaired'] == [] and result['output_file'] is None
    assert result['remaining_messages'] == [{'ID': 'OTHER'}]
    assert source.read_bytes() == original and set(tmp_path.iterdir()) == {source}


def test_existing_metadata_preserved(monkeypatch, build, tmp_path):
    source = build()
    with ZipFile(source) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
    with ZipFile(source, 'w') as archive:
        archive.comment = b'archive comment'
        for entry, data in entries:
            entry.comment = b'entry comment'
            entry.date_time = (2003, 2, 4, 6, 8, 10)
            entry.external_attr = 0o100640 << 16
            if entry.filename != 'mimetype':
                entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, data)
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['repaired'] == DIAGNOSIS
    check_preservation(source, output, 'OPS/package.opf')


def test_non_mimetype_extra_refused(monkeypatch, build, tmp_path):
    source = build()
    with ZipFile(source) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
    with ZipFile(source, 'w') as archive:
        for entry, data in entries:
            if entry.filename != 'mimetype':
                entry.extra = struct.pack('<HH', 0xbeef, 0)
            archive.writestr(entry, data)
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == DIAGNOSIS and result['repaired'] == []
    assert result['reason'] == 'unsafe_metadata_preservation'
    assert not output.exists() and set(tmp_path.iterdir()) == {source}


def test_generated_xml_semantics_guard(monkeypatch):
    monkeypatch.setattr(repairer.ElementTree, 'tostring', lambda *args, **kwargs:
                        b'<container xmlns="wrong"><rootfiles><rootfile/></rootfiles></container>')
    with pytest.raises(UnsafeZipError, match='Generated container semantics'):
        repairer._build_container_xml('OPS/package.opf')


@pytest.mark.parametrize('ids', [(), ('RSC-002', 'PKG-005'), ('RSC-002', 'PKG-006'), ('RSC-002', 'PKG-007')])
def test_repair_rechecks_message_guards_even_if_diagnosis_is_injected(monkeypatch, build, tmp_path, ids):
    source = build()
    calls = fake_validation(monkeypatch, ids)
    monkeypatch.setattr(repairer, 'diagnose_epub', lambda *args: DIAGNOSIS)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'unsafe_container_creation'
    assert result['repaired'] == [] and result['output_file'] is None
    assert len(calls) == 1 and not output.exists()


def test_rebuild_is_deterministic(monkeypatch, build, tmp_path):
    source = build()
    outputs = [tmp_path / 'first.epub', tmp_path / 'second.epub']
    for output in outputs:
        fake_validation(monkeypatch)
        result = repairer.repair_epub(str(source), str(output))
        assert result['repaired'] == DIAGNOSIS
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
