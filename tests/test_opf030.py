from copy import copy
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

import pytest

from src import diagnoser, repairer, service
from src.identifier_mutation import correct_unique_identifier
from src.validator import EPUBCheckError, validate_epub
from src.zip_inspection import UnsafeZipError

FIXTURES = Path(__file__).parent / 'fixtures/opf030'
PACKAGE = 'EPUB/package.opf'
FAMILY = 'package_unique_identifier_unresolved'
METADATA = ('date_time', 'compress_type', 'comment', 'extra', 'internal_attr',
            'external_attr', 'create_system', 'create_version', 'extract_version')


def report(path=PACKAGE):
    return dict(valid=False, publication={}, messages=[dict(ID='OPF-030',
        message='The unique-identifier "absent" was not found.', locations=[dict(path=path)])])


def contents(name='unresolved'):
    with ZipFile(FIXTURES / (name + '.epub')) as archive:
        return {entry.filename: archive.read(entry) for entry in archive.infolist()}


@pytest.fixture
def build(tmp_path):
    def create(replacements=None, fixture='unresolved', duplicate=None):
        source = tmp_path / 'input.epub'
        with ZipFile(FIXTURES / (fixture + '.epub')) as archive:
            entries = [(copy(entry), archive.read(entry)) for entry in archive.infolist()]
            comment = archive.comment
        with ZipFile(source, 'w') as archive:
            archive.comment = comment
            names = set()
            for entry, data in entries:
                names.add(entry.filename)
                data = (replacements or {}).get(entry.filename, data)
                if data is not None:
                    archive.writestr(entry, data)
            for name, data in (replacements or {}).items():
                if name not in names and data is not None:
                    archive.writestr(name, data)
            if duplicate:
                with pytest.warns(UserWarning, match='Duplicate name'):
                    archive.writestr(duplicate, b'duplicate')
        return source
    return create


def stub(monkeypatch, initial=None, final=True):
    calls = []
    def validate(path):
        calls.append(path)
        if len(calls) == 1:
            return initial if initial is not None else report()
        if isinstance(final, Exception):
            raise final
        return dict(valid=final, publication={}, messages=[] if final else [{'ID': 'UNRELATED'}])
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    return calls


def refused(source, output, before, result):
    assert source.read_bytes() == before and not output.exists()
    assert result['repaired'] == [] and result['output_file'] is None
    assert set(source.parent.iterdir()) == {source}


def test_real_positive_preserves_service_contract_and_all_other_bytes(tmp_path):
    source = FIXTURES / 'unresolved.epub'
    before = source.read_bytes()
    initial = validate_epub(str(source))
    assert initial['fatal_count'] == 0 and initial['error_count'] == 2
    assert [message['ID'] for message in initial['messages']] == ['OPF-030', 'RSC-005']
    assert initial['messages'][1]['message'] == ('Error while parsing file: package element unique-identifier '
        'attribute does not resolve to a dc:identifier element (given reference was "absent")')
    inspection = service.inspect_epub(str(source))
    assert inspection['repairable'] and inspection['safe_repairs'] == [FAMILY]
    assert list(tmp_path.iterdir()) == []
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result == dict(original_valid=False, detected=[FAMILY], repaired=[FAMILY],
                          final_valid=True, output_file=str(output), remaining_messages=[], reason=None)
    final = validate_epub(str(output))
    assert final['valid'] and final['fatal_count'] == final['error_count'] == 0
    assert source.read_bytes() == before
    with ZipFile(source) as original, ZipFile(output) as changed:
        assert original.namelist() == changed.namelist() and original.comment == changed.comment
        for entry in original.infolist():
            assert all(getattr(entry, key) == getattr(changed.getinfo(entry.filename), key) for key in METADATA)
            expected = original.read(entry)
            if entry.filename == PACKAGE:
                expected = expected.replace(b"unique-identifier = 'absent'", b"unique-identifier = 'id'")
            assert changed.read(entry.filename) == expected


@pytest.mark.parametrize('change,reason', [
    ('multiple', 'opf030_identifier_ambiguous'), ('uuid-isbn', 'opf030_identifier_ambiguous'),
    ('no-identifier', 'opf030_identifier_missing'), ('no-id', 'opf030_identifier_id_invalid'),
    ('empty-id', 'opf030_identifier_id_invalid'), ('invalid-id', 'opf030_identifier_id_invalid'),
    ('unicode-id', 'opf030_identifier_id_invalid'), ('colon-id', 'opf030_identifier_id_invalid'),
    ('collision', 'opf030_identifier_ambiguous'), ('xml-id-collision', 'opf030_identifier_ambiguous'),
    ('empty-value', 'opf030_identifier_value_invalid'), ('edge-whitespace', 'opf030_identifier_value_invalid'),
    ('control-value', 'opf030_identifier_value_invalid'), ('child', 'opf030_identifier_value_invalid'),
    ('comment', 'opf030_identifier_value_invalid'), ('identifier-pi', 'opf030_identifier_value_invalid'),
    ('missing-reference', 'opf030_unique_identifier_missing'), ('empty-reference', 'opf030_unique_identifier_missing'),
    ('wrong-location', 'opf030_evidence_ambiguous'), ('no-location', 'opf030_evidence_ambiguous'),
    ('null-location', 'opf030_evidence_ambiguous'), ('extra-location', 'opf030_evidence_ambiguous'),
    ('wrong-message', 'opf030_evidence_ambiguous'), ('reference-exists', 'opf030_reference_already_exists'),
    ('multiple-metadata', 'opf030_identifier_ambiguous'), ('nested-identifier', 'opf030_identifier_ambiguous'),
    ('multiple-rootfiles', 'opf030_package_ambiguous'), ('missing-package', 'opf030_package_ambiguous'),
    ('nested-package', 'opf030_package_ambiguous'),
    ('malformed', 'unsafe_opf030_mutation'), ('dtd', 'unsafe_opf030_mutation'),
    ('non-utf8', 'unsafe_opf030_mutation'), ('xml-base', 'unsafe_opf030_mutation'),
    ('depth', 'unsafe_opf030_mutation'), ('unsafe-path', 'unsafe_opf030_archive'),
    ('signed', 'signatures_or_encryption'), ('encrypted', 'signatures_or_encryption'),
    ('opf014', 'opf030_conflicting_repair'), ('rsc008', 'opf030_conflicting_repair'),
    ('invalid-uuid-evidence', 'opf030_identifier_value_invalid'),
    ('wrong-companion', 'opf030_evidence_ambiguous'),
])
def test_proof_failures_refuse_without_any_mutation(change, reason, build, tmp_path, monkeypatch):
    files, initial, edits = contents(), report(), {}
    data = files[PACKAGE]
    identifier = b'<dc:identifier id="id">urn:example:rsc008</dc:identifier>'
    if change == 'multiple': data = contents('multiple-identifiers')[PACKAGE]
    elif change == 'uuid-isbn': data = contents('uuid-and-isbn')[PACKAGE]
    elif change == 'no-identifier': data = data.replace(identifier, b'')
    elif change == 'no-id': data = data.replace(b'dc:identifier id="id"', b'dc:identifier')
    elif change == 'empty-id': data = data.replace(b'dc:identifier id="id"', b'dc:identifier id=""')
    elif change == 'invalid-id': data = data.replace(b'dc:identifier id="id"', b'dc:identifier id="9bad"')
    elif change == 'unicode-id': data = data.replace(b'dc:identifier id="id"', 'dc:identifier id="شناسه"'.encode())
    elif change == 'colon-id': data = data.replace(b'dc:identifier id="id"', b'dc:identifier id="a:b"')
    elif change == 'collision': data = data.replace(b'<dc:title>', b'<dc:title id="id">')
    elif change == 'xml-id-collision': data = data.replace(b'<dc:title>', b'<dc:title xml:id="id">')
    elif change == 'empty-value': data = data.replace(b'urn:example:rsc008', b'')
    elif change == 'edge-whitespace': data = data.replace(b'urn:example:rsc008', b' urn:example:rsc008 ')
    elif change == 'control-value': data = data.replace(b'urn:example:rsc008', b'urn:example:&#x85;')
    elif change == 'child': data = data.replace(b'urn:example:rsc008', b'<dc:title>value</dc:title>')
    elif change == 'comment': data = data.replace(b'urn:example:rsc008', b'urn:example:<!-- split -->rsc008')
    elif change == 'identifier-pi': data = data.replace(b'urn:example:rsc008', b'urn:example:<?test value?>rsc008')
    elif change == 'missing-reference': data = contents('missing-reference')[PACKAGE]
    elif change == 'empty-reference': data = contents('empty-reference')[PACKAGE]
    elif change == 'wrong-location': initial = report('other.opf')
    elif change == 'no-location': initial['messages'][0].pop('locations')
    elif change == 'null-location': initial['messages'][0]['locations'] = [None]
    elif change == 'extra-location': initial['messages'][0]['additionalLocations'] = 1
    elif change == 'wrong-message': initial['messages'][0]['message'] = None
    elif change == 'reference-exists': data = data.replace(b'<dc:title>', b'<dc:title id="absent">')
    elif change == 'multiple-metadata': data = data.replace(b'</metadata>', b'</metadata><metadata/>')
    elif change == 'nested-identifier': data = data.replace(identifier, b'<wrapper>' + identifier + b'</wrapper>')
    elif change == 'multiple-rootfiles':
        edits['META-INF/container.xml'] = files['META-INF/container.xml'].replace(b'</rootfiles>', b'<rootfile full-path="other.opf" media-type="application/oebps-package+xml"/></rootfiles>')
    elif change == 'missing-package': data = None
    elif change == 'nested-package': data = data.replace(b'</manifest>', b'<package/></manifest>')
    elif change == 'malformed': data = b'<package'
    elif change == 'dtd': data = b'<!DOCTYPE package [<!ENTITY id "id">]>' + data
    elif change == 'non-utf8': data = b'<?xml version="1.0" encoding="ISO-8859-1"?>' + data
    elif change == 'xml-base': data = data.replace(b'<manifest>', b'<manifest xml:base="./">')
    elif change == 'depth': data = data.replace(b'</manifest>', b'<x>' * 129 + b'</x>' * 129 + b'</manifest>')
    elif change == 'unsafe-path': edits['../other'] = b'untouched'
    elif change == 'signed': edits['META-INF/signatures.xml'] = b'<signatures/>'
    elif change == 'encrypted': edits['META-INF/encryption.xml'] = b'<encryption/>'
    elif change == 'opf014': initial['messages'].append({'ID': 'OPF-014'})
    elif change == 'rsc008':
        with ZipFile(FIXTURES.parent / 'rsc008/png.epub') as archive:
            for path in ('EPUB/chapter.xhtml', 'EPUB/images/resource.png'):
                edits[path] = archive.read(path)
        initial['messages'].append({'ID': 'RSC-008',
            'message': 'Referenced resource "EPUB/images/resource.png" is not declared in the OPF manifest.',
            'locations': [{'path': 'EPUB/chapter.xhtml'}]})
    elif change == 'invalid-uuid-evidence': initial['messages'].append({'ID': 'OPF-085'})
    elif change == 'wrong-companion':
        initial['messages'].append({'ID': 'RSC-005', 'message': 'Error while parsing file: package element unique-identifier attribute does not resolve to another reference', 'locations': [{'path': PACKAGE}]})
    edits[PACKAGE] = data
    source = build(edits)
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    assert FAMILY not in diagnoser.diagnose_epub(str(source), initial)
    stub(monkeypatch, initial)
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == reason
    refused(source, output, before, result)


@pytest.mark.parametrize('final,reason', [(False, 'revalidation_failed'),
                                        (EPUBCheckError('checker unavailable'), 'epubcheck_failure')])
def test_revalidation_failure_never_publishes(final, reason, build, tmp_path, monkeypatch):
    source, output = build(), tmp_path / 'out.epub'
    before = source.read_bytes()
    calls = stub(monkeypatch, final=final)
    result = service.repair_epub_file(str(source), str(output))
    assert len(calls) == 2 and result['reason'] == reason
    refused(source, output, before, result)


def test_existing_output_not_overwritten(build, tmp_path, monkeypatch):
    source, output = build(), tmp_path / 'out.epub'
    before = source.read_bytes()
    output.write_bytes(b'existing')
    calls = stub(monkeypatch)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'output_exists' and len(calls) == 1
    assert output.read_bytes() == b'existing' and source.read_bytes() == before


def test_duplicate_archive_refused(build, tmp_path, monkeypatch):
    source, output = build(duplicate=PACKAGE), tmp_path / 'out.epub'
    before = source.read_bytes()
    assert diagnoser.diagnose_epub(str(source), report()) == []
    stub(monkeypatch)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'duplicate_zip_entries'
    refused(source, output, before, result)


def test_no_evidence_never_retargets_broken_reference(build, tmp_path, monkeypatch):
    source, output = build(), tmp_path / 'out.epub'
    before = source.read_bytes()
    initial = dict(valid=False, publication={}, messages=[{'ID': 'UNSUPPORTED'}])
    assert diagnoser.diagnose_epub(str(source), initial) == []
    stub(monkeypatch, initial)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'no_supported_repair'
    refused(source, output, before, result)


def test_already_resolved_fake_evidence_refused(build):
    source = build(fixture='already-resolved')
    initial = report()
    initial['messages'][0]['message'] = 'The unique-identifier "id" was not found.'
    assert diagnoser.diagnose_epub(str(source), initial) == []
    with ZipFile(source) as archive, pytest.raises(UnsafeZipError) as exc:
        diagnoser._unique_identifier_replacement(archive, initial)
    assert exc.value.reason == 'opf030_reference_already_exists'


def test_attribute_quotes_entities_comments_and_prefix_are_preserved():
    data = contents()[PACKAGE]
    data = b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\n<!-- unchanged -->' + data
    data = data.replace(b"'absent'", b"'abs&#101;nt'")
    data = data.replace(b'urn:example:rsc008', b'urn:example:a&amp;b')
    changed = correct_unique_identifier(data, PACKAGE, report()['messages'])
    assert changed == data.replace(b"'abs&#101;nt'", b"'id'")
    prefixed = data.replace(b'xmlns="http://www.idpf.org/2007/opf"', b'xmlns:o="http://www.idpf.org/2007/opf" xmlns="http://www.idpf.org/2007/opf"')
    prefixed = prefixed.replace(b'<package ', b'<o:package ').replace(b'</package>', b'</o:package>')
    assert correct_unique_identifier(prefixed, PACKAGE, report()['messages']) == prefixed.replace(b"'abs&#101;nt'", b"'id'")


@pytest.mark.parametrize('data', [b' ' * (8 * 1024 * 1024 + 1), contents()[PACKAGE].decode().encode('utf-16')])
def test_oversized_and_utf16_xml_refused(data):
    with pytest.raises(UnsafeZipError) as exc:
        correct_unique_identifier(data, PACKAGE, report()['messages'])
    assert exc.value.reason == 'unsafe_opf030_mutation'


def test_inspect_requires_full_proof_and_creates_no_outputs(build, monkeypatch):
    source = build(fixture='multiple-identifiers')
    before = source.read_bytes()
    monkeypatch.setattr(service, 'validate_epub', lambda path: {**report(), 'fatal_count': 0, 'error_count': 1, 'warning_count': 0})
    result = service.inspect_epub(str(source))
    assert not result['repairable'] and result['safe_repairs'] == []
    assert source.read_bytes() == before and set(source.parent.iterdir()) == {source}


def test_fixture_reproduction(tmp_path):
    for directory in ('opf030', 'rsc008', 'ncx001'):
        destination = tmp_path / directory
        destination.mkdir()
        shutil.copyfile(FIXTURES.parent / directory / 'build_fixtures.py', destination / 'build_fixtures.py')
    subprocess.run([sys.executable, str(tmp_path / 'opf030/build_fixtures.py')], check=True)
    for fixture in FIXTURES.glob('*.epub'):
        assert (tmp_path / 'opf030' / fixture.name).read_bytes() == fixture.read_bytes()


@pytest.mark.parametrize('name', ['ncx-matching', 'ncx-mismatching'])
def test_real_ncx_relation_never_mutates_ncx_or_runs_a_second_repair(name, tmp_path, monkeypatch):
    source = FIXTURES / (name + '.epub')
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    initial = validate_epub(str(source))
    assert initial['fatal_count'] == 0 and initial['error_count'] == 1
    assert [msg['ID'] for msg in initial['messages']] == ['OPF-030']
    assert diagnoser.diagnose_epub(str(source), initial) == [FAMILY]
    snapshots = []
    def validate(path):
        if str(path) != str(source):
            with ZipFile(path) as archive:
                snapshots.append({entry.filename: archive.read(entry) for entry in archive.infolist()})
        return validate_epub(path)
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    result = service.repair_epub_file(str(source), str(output))
    assert len(snapshots) == 1  # One revalidation; no iterative NCX repair.
    assert snapshots[0] == {key: value.replace(b'unique-identifier="absent"', b'unique-identifier="book-id"')
                           if key == PACKAGE else value for key, value in contents(name).items()}
    assert source.read_bytes() == before
    if name == 'ncx-matching':
        assert result['repaired'] == [FAMILY] and result['final_valid']
        final = validate_epub(str(output))
        assert final['valid'] and final['fatal_count'] == final['error_count'] == 0
    else:
        assert result['reason'] == 'revalidation_failed' and not result['final_valid']
        assert [msg['ID'] for msg in result['remaining_messages']] == ['NCX-001']
        assert result['repaired'] == [] and result['output_file'] is None
        assert not output.exists() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('name', ['missing-reference', 'empty-reference', 'already-resolved'])
def test_real_missing_empty_and_correct_reference_scope(name, tmp_path):
    source = FIXTURES / (name + '.epub')
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    initial = validate_epub(str(source))
    codes = {message['ID'] for message in initial['messages']}
    if name == 'already-resolved':
        assert initial['valid'] and 'OPF-030' not in codes
    else:
        # Bundled 5.4.0 DOES also emit OPF-030 here; presence alone is insufficient.
        assert {'OPF-030', 'OPF-048', 'RSC-005'} <= codes
    assert diagnoser.diagnose_epub(str(source), initial) == []
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == ('already_valid' if name == 'already-resolved' else 'opf030_unique_identifier_missing')
    assert source.read_bytes() == before and not output.exists() and result['repaired'] == []


def test_initial_opf030_ncx001_preserves_existing_ncx_refusal(build, tmp_path, monkeypatch):
    source, output = build(), tmp_path / 'out.epub'
    before = source.read_bytes()
    initial = report()
    initial['messages'].append({'ID': 'NCX-001', 'locations': [{'path': 'EPUB/toc.ncx'}]})
    assert diagnoser.diagnose_epub(str(source), initial) == []
    stub(monkeypatch, initial)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'ncx_identifier_invalid'
    refused(source, output, before, result)


def test_rebuild_error_does_not_change_original(build, tmp_path, monkeypatch):
    source, output = build(), tmp_path / 'out.epub'
    before = source.read_bytes()
    stub(monkeypatch)
    original_write = ZipFile.writestr
    def write(archive, entry, data, *args, **kwargs):
        if getattr(entry, 'filename', entry) == PACKAGE:
            raise OSError('simulated OPF write failure')
        return original_write(archive, entry, data, *args, **kwargs)
    monkeypatch.setattr(ZipFile, 'writestr', write)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'output_write_failed'
    refused(source, output, before, result)


def test_real_unrelated_rsc005_is_not_a_companion_repair(tmp_path):
    source, output = FIXTURES / 'unrelated-error.epub', tmp_path / 'out.epub'
    before = source.read_bytes()
    initial = validate_epub(str(source))
    assert [msg['ID'] for msg in initial['messages']].count('RSC-005') == 2
    assert 'OPF-030' in {msg['ID'] for msg in initial['messages']}
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == 'revalidation_failed' and result['final_valid'] is False
    assert [msg['ID'] for msg in result['remaining_messages']] == ['RSC-005']
    assert result['remaining_messages'][0]['locations'][0]['path'] == 'EPUB/chapter.xhtml'
    assert result['repaired'] == [] and result['output_file'] is None
    assert not output.exists() and source.read_bytes() == before


def test_extra_fields_refused_before_mutation(build, tmp_path, monkeypatch):
    import struct
    source = build()
    with ZipFile(source) as archive:
        entries = [(copy(entry), archive.read(entry)) for entry in archive.infolist()]
    with ZipFile(source, 'w') as archive:
        for entry, data in entries:
            if entry.filename == PACKAGE:
                entry.extra = struct.pack('<HHBI', 0x5455, 5, 1, 1700000000)
            archive.writestr(entry, data)
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    assert diagnoser.diagnose_epub(str(source), report()) == []
    stub(monkeypatch)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'unsafe_opf030_archive'
    refused(source, output, before, result)


def test_real_deferred_invalid_uuid_warning_invalidates_identifier_proof(tmp_path):
    source, output = FIXTURES / 'invalid-uuid.epub', tmp_path / 'out.epub'
    before = source.read_bytes()
    initial = validate_epub(str(source))
    assert [message['ID'] for message in initial['messages']] == ['OPF-030', 'RSC-005']
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == 'opf030_identifier_value_invalid'
    assert result['final_valid'] is True  # Preserve the actual validator's semantics.
    assert [(msg['ID'], msg['severity']) for msg in result['remaining_messages']] == [('OPF-085', 'WARNING')]
    assert result['repaired'] == [] and result['output_file'] is None
    assert source.read_bytes() == before and not output.exists() and list(tmp_path.iterdir()) == []
