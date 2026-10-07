from copy import copy
import re
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from src import diagnoser, repairer, service
from src.resource_manifest_mutation import _css_urls, _media_type
from src.validator import EPUBCheckError, validate_epub
from src.zip_inspection import UnsafeZipError

FIXTURES = Path(__file__).parent / 'fixtures/rsc008'
PACKAGE = 'EPUB/package.opf'
CHAPTER = 'EPUB/chapter.xhtml'
TARGET = 'EPUB/images/resource.png'
FAMILY = 'manifest_missing_referenced_resource'
METADATA = ('date_time', 'compress_type', 'comment', 'extra', 'internal_attr',
            'external_attr', 'create_system', 'create_version', 'extract_version')


def report(target=TARGET, location=CHAPTER):
    return dict(valid=False, publication={}, messages=[dict(
        ID='RSC-008', message=f'Referenced resource "{target}" is not declared in the OPF manifest.',
        locations=[dict(path=location)])])


def contents(name='png'):
    with ZipFile(FIXTURES / (name + '.epub')) as archive:
        return {entry.filename: archive.read(entry) for entry in archive.infolist()}


@pytest.fixture
def build(tmp_path):
    def create(replacements=None, fixture='png', duplicate=None):
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
    assert source.read_bytes() == before
    assert not output.exists()
    assert result['repaired'] == [] and result['output_file'] is None
    assert set(source.parent.iterdir()) == {source}


@pytest.mark.parametrize('name,count', [('png', 1), ('jpeg', 1), ('multiple', 3),
                                       ('svg', 1), ('css', 1), ('xhtml', 1)])
def test_real_checker_repair_and_service_contract(name, count, tmp_path):
    source = FIXTURES / (name + '.epub')
    before = source.read_bytes()
    initial = validate_epub(str(source))
    assert initial['fatal_count'] == 0 and initial['error_count'] == count
    assert [msg['ID'] for msg in initial['messages']] == ['RSC-008'] * count
    assert diagnoser.diagnose_epub(str(source), initial) == [FAMILY]
    inspection = service.inspect_epub(str(source))
    assert inspection['safe_repairs'] == [FAMILY] and inspection['repairable'] is True
    assert not list(tmp_path.iterdir())
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result == dict(original_valid=False, detected=[FAMILY], repaired=[FAMILY],
                          final_valid=True, output_file=str(output), remaining_messages=[], reason=None)
    final = validate_epub(str(output))
    assert final['valid'] and final['fatal_count'] == final['error_count'] == 0
    assert source.read_bytes() == before
    with ZipFile(source) as original, ZipFile(output) as changed:
        assert original.namelist() == changed.namelist()
        assert original.comment == changed.comment
        for entry in original.infolist():
            assert all(getattr(entry, key) == getattr(changed.getinfo(entry.filename), key) for key in METADATA)
            if entry.filename != PACKAGE:
                assert original.read(entry) == changed.read(entry.filename)
        old, new = original.read(PACKAGE), changed.read(PACKAGE)
        # Removing the exact inserted substring reproduces every original OPF byte.
        offset = old.index(b'</manifest>')
        inserted = new[offset:offset + len(new) - len(old)]
        assert new == old[:offset] + inserted + old[offset:]
        root = ElementTree.fromstring(new)
        items = root.findall('./{http://www.idpf.org/2007/opf}manifest/{http://www.idpf.org/2007/opf}item')
        added = [item for item in items if item.get('id').startswith('epub-doctor-resource-')]
        assert len(added) == count
        assert all(item.get('href').startswith('images/') for item in added)


@pytest.mark.parametrize('change,reason', [
    ('missing-location', 'rsc008_evidence_ambiguous'),
    ('wrong-location', 'rsc008_evidence_ambiguous'),
    ('wrong-message', 'rsc008_evidence_ambiguous'),
    ('null-message', 'rsc008_evidence_ambiguous'),
    ('malformed-location', 'rsc008_evidence_ambiguous'),
    ('additional-locations', 'rsc008_evidence_ambiguous'),
    ('wrong-target', 'rsc008_resource_missing'),
    ('existing-unreferenced-target', 'rsc008_evidence_ambiguous'),
    ('missing-resource', 'rsc008_resource_missing'),
    ('directory', 'rsc008_evidence_ambiguous'),
    ('already-declared', 'rsc008_resource_already_declared'),
    ('unknown-type', 'rsc008_media_type_unsupported'),
    ('wrong-magic', 'rsc008_media_type_unsupported'),
    ('empty', 'rsc008_resource_ambiguous'),
    ('unsafe-target', 'rsc008_evidence_ambiguous'),
    ('remote', 'rsc008_remote_resource'),
    ('data-uri', 'rsc008_remote_resource'),
    ('multiple-rootfiles', 'rsc008_package_ambiguous'),
    ('xml-base', 'unsafe_rsc008_manifest_mutation'),
    ('alias', 'rsc008_resource_ambiguous'),
    ('html-base', 'unsafe_rsc008_manifest_mutation'),
    ('dtd', 'unsafe_rsc008_manifest_mutation'),
    ('epub2', 'unsafe_rsc008_manifest_mutation'),
    ('non-utf8', 'unsafe_rsc008_manifest_mutation'),
    ('duplicate-id', 'rsc008_package_ambiguous'),
    ('opf014', 'rsc008_properties_unsupported'),
    ('signed', 'signatures_or_encryption'),
    ('encrypted', 'signatures_or_encryption'),
    ('corrupt-package', 'unsafe_rsc008_manifest_mutation'),
])
def test_precise_refusals_are_atomic(change, reason, build, tmp_path, monkeypatch):
    files, initial, edits = contents(), report(), {}
    msg = initial['messages'][0]
    if change == 'missing-location': msg['locations'] = []
    elif change == 'wrong-location': msg['locations'] = [{'path': 'EPUB/nav.xhtml'}]
    elif change == 'wrong-message': msg['message'] = 'Undeclared resource'
    elif change == 'null-message': msg['message'] = None
    elif change == 'malformed-location': msg['locations'] = [None]
    elif change == 'additional-locations': msg['additionalLocations'] = 1
    elif change == 'wrong-target': initial = report('EPUB/images/wrong.png')
    elif change == 'existing-unreferenced-target':
        initial = report('EPUB/images/wrong.png'); edits['EPUB/images/wrong.png'] = files[TARGET]
    elif change == 'missing-resource': edits[TARGET] = None
    elif change == 'directory':
        edits[TARGET] = None; edits[TARGET + '/'] = b''; initial = report(TARGET + '/')
    elif change == 'already-declared':
        edits[PACKAGE] = files[PACKAGE].replace(b'</manifest>', b'<item id="image" href="images/resource.png" media-type="image/png"/></manifest>')
    elif change == 'unknown-type':
        initial = report('EPUB/images/resource.bin'); edits['EPUB/images/resource.bin'] = b'unknown'
    elif change == 'wrong-magic': edits[TARGET] = b'not a PNG'
    elif change == 'empty': edits[TARGET] = b''
    elif change == 'unsafe-target': initial = report('EPUB/../resource.png')
    elif change == 'remote': initial = report('https://example.com/resource.png')
    elif change == 'data-uri': initial = report('data:image/png,hello')
    elif change == 'multiple-rootfiles':
        edits['META-INF/container.xml'] = files['META-INF/container.xml'].replace(b'</rootfiles>', b'<rootfile full-path="other.opf" media-type="application/oebps-package+xml"/></rootfiles>')
    elif change == 'xml-base': edits[PACKAGE] = files[PACKAGE].replace(b'<manifest>', b'<manifest xml:base="EPUB/">')
    elif change == 'alias':
        edits[PACKAGE] = files[PACKAGE].replace(b'</manifest>', b'<item id="alias" href="./chapter.xhtml" media-type="application/xhtml+xml"/></manifest>')
    elif change == 'html-base': edits[CHAPTER] = files[CHAPTER].replace(b'</head>', b'<base href="../"/></head>')
    elif change == 'dtd': edits[PACKAGE] = b'<!DOCTYPE package>' + files[PACKAGE]
    elif change == 'epub2': edits[PACKAGE] = files[PACKAGE].replace(b'version="3.0"', b'version="2.0"')
    elif change == 'non-utf8': edits[PACKAGE] = b'<?xml version="1.0" encoding="ISO-8859-1"?>' + files[PACKAGE]
    elif change == 'duplicate-id': edits[PACKAGE] = files[PACKAGE].replace(b'id="other"', b'id="id"')
    elif change == 'opf014': initial['messages'].append({'ID': 'OPF-014'})
    elif change == 'signed': edits['META-INF/signatures.xml'] = b'<signatures/>'
    elif change == 'encrypted': edits['META-INF/encryption.xml'] = b'<encryption/>'
    elif change == 'corrupt-package': edits[PACKAGE] = b'<package'
    source = build(edits)
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    assert FAMILY not in diagnoser.diagnose_epub(str(source), initial)
    stub(monkeypatch, initial)
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == reason
    refused(source, output, before, result)


def test_duplicate_zip_refused(build, tmp_path, monkeypatch):
    source = build(duplicate=TARGET)
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    assert diagnoser.diagnose_epub(str(source), report()) == []
    stub(monkeypatch)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'duplicate_zip_entries'
    refused(source, output, before, result)


def test_all_targets_proved_before_any_repair(build, tmp_path, monkeypatch):
    initial = report()
    initial['messages'] += report('EPUB/images/missing.jpg')['messages']
    source = build()
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    assert diagnoser.diagnose_epub(str(source), initial) == []
    calls = stub(monkeypatch, initial)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'rsc008_resource_missing' and len(calls) == 1
    refused(source, output, before, result)


@pytest.mark.parametrize('final,reason', [(False, 'revalidation_failed'),
                                        (EPUBCheckError('checker unavailable'), 'epubcheck_failure')])
def test_revalidation_failure_never_publishes(final, reason, build, tmp_path, monkeypatch):
    source = build()
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    calls = stub(monkeypatch, final=final)
    result = repairer.repair_epub(str(source), str(output))
    assert len(calls) == 2 and result['reason'] == reason
    refused(source, output, before, result)


def test_deduplication_and_ids_are_deterministic(build):
    files = contents()
    files[PACKAGE] = files[PACKAGE].replace(b'id="other"', b'id="epub-doctor-resource-1"')
    source = build(files)
    initial = report()
    initial['messages'] *= 3
    with ZipFile(source) as archive:
        first = diagnoser._referenced_resource_replacement(archive, initial)[1]
        second = diagnoser._referenced_resource_replacement(archive, initial)[1]
    assert first == second
    assert first.count(b'id="epub-doctor-resource-2"') == 1
    assert first.count(b'href="images/resource.png"') == 1


def test_prefixed_opf_and_relative_href(build):
    files = contents()
    package = files[PACKAGE].replace(b'xmlns="http://www.idpf.org/2007/opf"', b'xmlns:o="http://www.idpf.org/2007/opf"')
    for tag in (b'package', b'metadata', b'meta', b'manifest', b'item', b'spine', b'itemref'):
        package = re.sub(rb'(<\/?)' + tag + rb'(?=[\s/>])', rb'\1o:' + tag, package)
    source = build({PACKAGE: None, 'EPUB/deep/package.opf': package.replace(b'href="', b'href="../'),
                    'META-INF/container.xml': files['META-INF/container.xml'].replace(PACKAGE.encode(), b'EPUB/deep/package.opf')})
    with ZipFile(source) as archive:
        path, changed = diagnoser._referenced_resource_replacement(archive, report())
    assert path == 'EPUB/deep/package.opf'
    assert b'<o:item id="epub-doctor-resource-1" href="../images/resource.png" media-type="image/png"/>' in changed


def test_no_rsc008_does_not_scan_undeclared_zip_members(build, tmp_path, monkeypatch):
    source = build()
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    initial = dict(valid=False, publication={}, messages=[{'ID': 'RSC-007'}])
    assert diagnoser.diagnose_epub(str(source), initial) == []
    stub(monkeypatch, initial)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'no_supported_repair'
    refused(source, output, before, result)


def test_real_unrelated_error_not_hidden(tmp_path):
    source = FIXTURES / 'unrelated-error.epub'
    before, output = source.read_bytes(), tmp_path / 'out.epub'
    initial = validate_epub(str(source))
    assert {'RSC-008', 'RSC-005'} <= {msg['ID'] for msg in initial['messages']}
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == 'revalidation_failed' and not result['final_valid']
    assert 'RSC-005' in {msg['ID'] for msg in result['remaining_messages']}
    assert source.read_bytes() == before and not output.exists() and result['repaired'] == []


def test_real_scripted_resource_refused(tmp_path):
    source = FIXTURES / 'scripted-xhtml.epub'
    initial = validate_epub(str(source))
    assert 'RSC-008' in {msg['ID'] for msg in initial['messages']}
    assert diagnoser.diagnose_epub(str(source), initial) == []
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == 'rsc008_properties_unsupported' and not output.exists()


def test_real_linked_xhtml_never_reconstructs_spine(tmp_path):
    source = FIXTURES / 'linked-xhtml.epub'
    initial = validate_epub(str(source))
    assert [msg['ID'] for msg in initial['messages']] == ['RSC-008']
    assert diagnoser.diagnose_epub(str(source), initial) == []
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == 'rsc008_resource_requires_spine' and not output.exists()


@pytest.mark.parametrize('extension,signature,media', [
    ('ttf', b'\x00\x01\x00\x00', 'font/ttf'), ('otf', b'OTTO', 'font/otf'),
    ('woff', b'wOFF', 'font/woff'), ('woff2', b'wOF2', 'font/woff2'),
])
def test_explicit_font_header_mapping(extension, signature, media):
    if extension in ('ttf', 'otf'):
        data = signature + b'\x00\x01' + bytes(6) + bytes(16)
    else:
        size = 44 if extension == 'woff' else 48
        data = signature + b'OTTO' + size.to_bytes(4, 'big') + b'\x00\x01' + bytes(size - 14)
    assert _media_type('EPUB/font.' + extension, data) == media
    with pytest.raises(UnsafeZipError, match='signature'):
        _media_type('EPUB/font.' + extension, b'wrong')


def test_html_requires_xhtml_namespace_and_xml():
    assert _media_type('chapter.html', contents('xhtml')['EPUB/images/resource.xhtml']) == 'application/xhtml+xml'
    with pytest.raises(UnsafeZipError):
        _media_type('chapter.html', b'<html><body>HTML syntax</body></html>')


def test_literal_css_font_reference_ignores_comments_and_strings():
    assert _css_urls(b'/* url(fake.woff) */ p{content:"url(fake.woff)"} @font-face{src:url("font.woff")}') == ['font.woff']
    for data in (b'@import "file.css";', b'p{background:url(\\66 oo.png)}', b'/* unfinished'):
        with pytest.raises(ValueError):
            _css_urls(data)


def test_companion_opf003_not_suppressed(build, tmp_path, monkeypatch):
    initial = report()
    initial['messages'].append({'ID': 'OPF-003',
                               'message': f'Item "{TARGET}" exists in the EPUB, but is not declared in the OPF manifest.',
                               'locations': [{'path': PACKAGE}]})
    source, output = build(), tmp_path / 'out.epub'
    calls = stub(monkeypatch, initial, final=False)
    result = repairer.repair_epub(str(source), str(output))
    assert len(calls) == 2 and result['reason'] == 'revalidation_failed' and not output.exists()


@pytest.mark.parametrize('target,location', [('EPUB/other.png', PACKAGE), (TARGET, 'other.opf')])
def test_unrelated_opf003_is_not_an_allowed_companion(target, location, build, tmp_path, monkeypatch):
    initial = report()
    initial['messages'].append({'ID': 'OPF-003',
                               'message': f'Item "{target}" exists in the EPUB, but is not declared in the OPF manifest.',
                               'locations': [{'path': location}]})
    source, output = build(), tmp_path / 'out.epub'
    before = source.read_bytes()
    assert diagnoser.diagnose_epub(str(source), initial) == []
    stub(monkeypatch, initial)
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'rsc008_companion_unsupported'
    refused(source, output, before, result)


def test_no_unreported_zip_resource_is_added(build):
    source = build({'EPUB/images/extra.png': contents()[TARGET]})
    with ZipFile(source) as archive:
        changed = diagnoser._referenced_resource_replacement(archive, report())[1]
    assert b'extra.png' not in changed


def test_unsafe_unrelated_zip_path_refused(build):
    source = build({'../unexpected': b'untouched'})
    assert diagnoser.diagnose_epub(str(source), report()) == []
    with ZipFile(source) as archive, pytest.raises(UnsafeZipError) as error:
        diagnoser._referenced_resource_replacement(archive, report())
    assert error.value.reason == 'unsafe_rsc008_archive'


def test_rebuild_failure_leaves_input_and_output_untouched(build, tmp_path, monkeypatch):
    source, output = build(), tmp_path / 'out.epub'
    before = source.read_bytes()
    stub(monkeypatch)
    original_write = ZipFile.writestr
    def write(archive, entry, data, *args, **kwargs):
        if getattr(entry, 'filename', entry) == PACKAGE:
            raise OSError('simulated OPF rebuild failure')
        return original_write(archive, entry, data, *args, **kwargs)
    monkeypatch.setattr(ZipFile, 'writestr', write)
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == 'output_write_failed'
    refused(source, output, before, result)


def test_css_referrer_proves_literal_font_target(build):
    files = contents()
    font = b'\x00\x01\x00\x00\x00\x01' + bytes(22)
    package = files[PACKAGE].replace(b'</manifest>', b'<item id="style" href="style.css" media-type="text/css"/></manifest>')
    source = build({PACKAGE: package, 'EPUB/style.css': b'@font-face{font-family:Test;src:url(font.ttf)}',
                    'EPUB/font.ttf': font})
    with ZipFile(source) as archive:
        changed = diagnoser._referenced_resource_replacement(archive, report('EPUB/font.ttf', 'EPUB/style.css'))[1]
    assert b'href="font.ttf" media-type="font/ttf"' in changed


def test_fixture_generation_is_reproducible(tmp_path):
    import shutil
    import subprocess
    import sys
    for source in [FIXTURES / 'build_fixtures.py', *FIXTURES.glob('*.b64')]:
        shutil.copyfile(source, tmp_path / source.name)
    subprocess.run([sys.executable, str(tmp_path / 'build_fixtures.py')], check=True)
    for source in FIXTURES.glob('*.epub'):
        assert (tmp_path / source.name).read_bytes() == source.read_bytes()


def test_inspect_repairable_only_for_complete_proof(build, monkeypatch):
    source = build({TARGET: None})
    before = source.read_bytes()
    monkeypatch.setattr(service, 'validate_epub', lambda path: {**report(), 'fatal_count': 0, 'error_count': 1, 'warning_count': 0})
    result = service.inspect_epub(str(source))
    assert result['safe_repairs'] == [] and result['repairable'] is False
    assert source.read_bytes() == before and set(source.parent.iterdir()) == {source}
