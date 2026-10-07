from copy import copy
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from src import diagnoser, repairer, service
from src.ncx_mutation import correct_ncx_identifier
from src.validator import EPUBCheckError, validate_epub
from src.zip_inspection import UnsafeZipError

FIXTURES = Path(__file__).parent / 'fixtures/ncx001'
PACKAGE, NCX = 'EPUB/package.opf', 'EPUB/toc.ncx'
FAMILY = 'ncx_identifier_mismatch'
CANONICAL = 'urn:example:canonical'
METADATA = ('date_time', 'compress_type', 'comment', 'extra', 'internal_attr',
            'external_attr', 'create_system', 'create_version', 'extract_version')


def report(path=NCX):
    return dict(valid=False, messages=[{'ID': 'NCX-001', 'locations': [{'path': path}]}], publication={})


def fixture_bytes(name='identifier-mismatch'):
    return (FIXTURES / (name + '.epub')).read_bytes()


@pytest.fixture
def build(tmp_path):
    def factory(replacements=None, fixture='identifier-mismatch'):
        path = tmp_path / 'input.epub'
        with ZipFile(FIXTURES / (fixture + '.epub')) as archive:
            entries = [(copy(entry), archive.read(entry)) for entry in archive.infolist()]
            comment = archive.comment
        with ZipFile(path, 'w') as archive:
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
        return path
    return factory


def stub(monkeypatch, initial=None, final=True):
    calls = []
    def validate(path):
        calls.append(path)
        if len(calls) == 1:
            return initial or report()
        if isinstance(final, Exception):
            raise final
        return dict(valid=final, messages=[] if final else [{'ID': 'OTHER'}], publication={})
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    return calls


def assert_refused(source, output, before, result):
    assert source.read_bytes() == before
    assert not output.exists()
    assert result['repaired'] == [] and result['output_file'] is None
    assert set(source.parent.iterdir()) == {source}


@pytest.mark.parametrize('fixture', ['identifier-mismatch', 'multiple-identifiers'])
def test_real_ncx001_repair_and_independent_checker(fixture, tmp_path):
    source = FIXTURES / (fixture + '.epub')
    before = source.read_bytes()
    initial = validate_epub(str(source))
    assert (initial['fatal_count'], initial['error_count'], initial['warning_count']) == (0, 1, 0)
    assert [msg['ID'] for msg in initial['messages']] == ['NCX-001']
    assert diagnoser.diagnose_epub(str(source), initial) == [FAMILY]
    inspected = service.inspect_epub(str(source))
    assert inspected['repairable'] is True and inspected['safe_repairs'] == [FAMILY]
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result['detected'] == result['repaired'] == [FAMILY]
    assert result['original_valid'] is False and result['final_valid'] is True
    assert result['output_file'] == str(output) and result['reason'] is None
    checked = validate_epub(str(output))
    assert checked['valid'] is True
    assert checked['fatal_count'] == checked['error_count'] == checked['warning_count'] == 0
    with ZipFile(source) as original, ZipFile(output) as changed:
        assert original.namelist() == changed.namelist()
        assert original.comment == changed.comment
        for entry in original.infolist():
            assert all(getattr(entry, field) == getattr(changed.getinfo(entry.filename), field) for field in METADATA)
            expected = original.read(entry)
            if entry.filename == NCX:
                expected = expected.replace(b'urn:example:wrong', CANONICAL.encode())
            assert changed.read(entry.filename) == expected
        root = ElementTree.fromstring(changed.read(NCX))
        uid = root.find('./{http://www.daisy.org/z3986/2005/ncx/}head/{http://www.daisy.org/z3986/2005/ncx/}meta')
        assert uid.get('content') == CANONICAL
    assert source.read_bytes() == before


@pytest.mark.parametrize('fixture,reason', [
    ('missing-unique-identifier', 'ncx_unique_identifier_missing'),
    ('unresolved-identifier', 'ncx_identifier_ambiguous'), ('empty-identifier', 'ncx_identifier_invalid'),
    ('missing-ncx', 'ncx_target_unreadable'), ('multiple-ncx', 'ncx_target_ambiguous'),
    ('missing-uid', 'unsafe_ncx_identifier_mutation'), ('duplicate-uid', 'unsafe_ncx_identifier_mutation'),
    ('missing-content', 'unsafe_ncx_identifier_mutation'),
])
def test_precise_proof_refusal_with_validator_evidence(fixture, reason, build, tmp_path, monkeypatch):
    source = build(fixture=fixture)
    before = source.read_bytes()
    calls = stub(monkeypatch)
    assert diagnoser.diagnose_epub(str(source), report()) == []
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] == reason and result['detail']
    assert result['detected'] == [] and len(calls) == 1
    assert_refused(source, output, before, result)


@pytest.mark.parametrize('fixture', ['missing-unique-identifier', 'unresolved-identifier', 'empty-identifier',
    'missing-ncx', 'multiple-ncx', 'missing-uid', 'duplicate-uid', 'missing-content'])
def test_real_checker_unsupported_fixtures_never_mutate(fixture, tmp_path):
    # Invalid OPFs/NCXs need not emit NCX-001 at all: never fabricate evidence.
    source = FIXTURES / (fixture + '.epub')
    before = source.read_bytes()
    initial = validate_epub(str(source))
    # EPUBCheck accepts a missing dtb:uid in this EPUB2 shape. That is still
    # not NCX-001 mismatch evidence and must never trigger UID construction.
    assert initial['valid'] is (fixture == 'missing-uid')
    # The unresolved OPF is now independently repairable by OPF-030, while
    # NCX repair still needs NCX-001 evidence and must not construct identifiers.
    expected = ['package_unique_identifier_unresolved'] if fixture == 'unresolved-identifier' else []
    assert diagnoser.diagnose_epub(str(source), initial) == expected
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result['reason'] and result['repaired'] == [] and result['output_file'] is None
    if fixture == 'unresolved-identifier':
        assert result['reason'] == 'revalidation_failed'
        assert [message['ID'] for message in result['remaining_messages']] == ['NCX-001']
    assert not output.exists() and source.read_bytes() == before


def test_no_ncx001_never_changes_matching_epub(tmp_path):
    source = FIXTURES / 'already-matching.epub'
    before = source.read_bytes()
    assert validate_epub(str(source))['valid'] is True
    result = service.repair_epub_file(str(source), str(tmp_path / 'out.epub'))
    assert result['reason'] == 'already_valid' and result['detected'] == result['repaired'] == []
    assert source.read_bytes() == before and not (tmp_path / 'out.epub').exists()


def test_mismatch_without_ncx001_is_not_detected(build, tmp_path, monkeypatch):
    source = build()
    before = source.read_bytes()
    # RSC-008 now has its own proven repair subset. Keep this NCX isolation
    # test on an unsupported code without changing its refusal assertions.
    validation = dict(valid=False, messages=[{'ID': 'RSC-007'}], publication={})
    stub(monkeypatch, validation)
    assert diagnoser.diagnose_epub(str(source), validation) == []
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'no_supported_repair'
    assert_refused(source, output, before, result)


@pytest.mark.parametrize('message_id', ['OPF-030', 'OPF-048', 'OPF-085'])
def test_invalid_identifier_validator_evidence_refuses_even_warning(message_id, build, tmp_path, monkeypatch):
    source = build()
    before = source.read_bytes()
    initial = report()
    initial['messages'].append({'ID': message_id, 'severity': 'WARNING'})
    stub(monkeypatch, initial)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'ncx_identifier_invalid'
    assert_refused(source, output, before, result)


@pytest.mark.parametrize('initial', [report('OTHER/toc.ncx'),
    dict(valid=False, messages=[{'ID': 'NCX-001', 'locations': []}], publication={}),
    dict(valid=False, messages=[{'ID': 'NCX-001', 'locations': [{'path': NCX}], 'additionalLocations': 1}], publication={})])
def test_report_paths_must_match_proven_ncx(initial, build, monkeypatch, tmp_path):
    source = build()
    before = source.read_bytes()
    stub(monkeypatch, initial)
    result = repairer.repair_epub(str(source), str(tmp_path / 'out.epub'))
    assert result['reason'] == 'ncx_evidence_ambiguous'
    assert_refused(source, tmp_path / 'out.epub', before, result)


@pytest.mark.parametrize('old,new,reason', [
    (b'urn:example:canonical', b' ', 'ncx_identifier_invalid'),
    (b'urn:example:canonical', b' urn:example:canonical ', 'ncx_identifier_invalid'),
    (b'urn:example:canonical', b'<b/>', 'ncx_identifier_invalid'),
    (b'unique-identifier="book-id"', b'unique-identifier="bad:id"', 'ncx_identifier_invalid'),
    (b'<metadata ', b'<metadata xml:base="other/" ', 'ncx_package_unsupported'),
    (b'<spine toc="ncx">', b'<spine>', 'ncx_target_ambiguous'),
    (b'<spine toc="ncx">', b'<spine toc="chapter">', 'ncx_target_ambiguous'),
    (b'href="toc.ncx"', b'href="https://example.org/toc.ncx"', 'ncx_target_ambiguous'),
    (b'href="toc.ncx"', b'href="../..//toc.ncx"', 'unsafe_ncx_identifier_proof'),
    (b'id="chapter"', b'id="ncx"', 'ncx_target_ambiguous'),
    (b'id="chapter"', b'id="book-id"', 'ncx_identifier_ambiguous'),
])
def test_package_proof_edge_refusals(old, new, reason, build, monkeypatch, tmp_path):
    with ZipFile(FIXTURES / 'identifier-mismatch.epub') as archive:
        package = archive.read(PACKAGE).replace(old, new)
    source = build({PACKAGE: package})
    before = source.read_bytes()
    stub(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == reason and result['detail']
    assert_refused(source, output, before, result)


@pytest.mark.parametrize('canonical', ['urn:example:new', 'ISBN-not-guessed', 'کتاب-۱۲۳', 'a&b<\'"c', 'a b'])
def test_byte_local_mutation_escaping_unicode_and_no_identifier_format_guess(canonical):
    with ZipFile(FIXTURES / 'identifier-mismatch.epub') as archive:
        original = archive.read(NCX)
    changed = correct_ncx_identifier(original, canonical)
    escaped = (canonical.replace('&', '&amp;').replace('<', '&lt;').replace('"', '&quot;').replace("'", '&apos;')).encode()
    assert changed == original.replace(b'urn:example:wrong', escaped)
    root = ElementTree.fromstring(changed)
    assert root.find('./{http://www.daisy.org/z3986/2005/ncx/}head/{http://www.daisy.org/z3986/2005/ncx/}meta').get('content') == canonical


@pytest.mark.parametrize('old,new', [
    (b" content='urn:example:wrong'", b''),
    (b'urn:example:wrong', b''), (b'urn:example:wrong', CANONICAL.encode()),
    (b'name="dtb:uid"', b'name="other"'),
    (b'<head>', b'<head xml:base="other/">'),
    (b'<head>', b'<head><meta name="dtb:uid" content="other"/>'),
    (b'<head>', b'<head></head><head>'),
    (b'<meta name="dtb:uid"', b'<meta content="duplicate" name="dtb:uid"'),
    (b"content='urn:example:wrong'/>", b"content='urn:example:wrong'><x/></meta>"),
    (b"content='urn:example:wrong'/>", b"content='urn:example:wrong'>text</meta>"),
    (b'2005/ncx/', b'wrong/'), (b'version="2005-1"', b'version="2000"'),
    (b'?>', b'?><!DOCTYPE ncx [<!ENTITY x "external">]>'),
])
def test_unsupported_ncx_shapes_are_refused(old, new):
    with ZipFile(FIXTURES / 'identifier-mismatch.epub') as archive:
        original = archive.read(NCX)
    with pytest.raises(UnsafeZipError, match='.') as exc:
        correct_ncx_identifier(original.replace(old, new), CANONICAL)
    assert exc.value.reason == 'unsafe_ncx_identifier_mutation'


def test_non_utf8_ncx_refused():
    with ZipFile(FIXTURES / 'identifier-mismatch.epub') as archive:
        data = archive.read(NCX).decode().replace('UTF-8', 'UTF-16').encode('utf-16')
    with pytest.raises(UnsafeZipError):
        correct_ncx_identifier(data, CANONICAL)


@pytest.mark.parametrize('formatting', ['bom', 'prefixed', 'double-quotes', 'entities'])
def test_byte_offsets_preserve_supported_xml_variants(formatting):
    with ZipFile(FIXTURES / 'identifier-mismatch.epub') as archive:
        data = archive.read(NCX)
    if formatting == 'bom':
        data = b'\xef\xbb\xbf' + data
    elif formatting == 'prefixed':
        data = data.replace(b'xmlns=', b'xmlns:n=').replace(b'<ncx', b'<n:ncx').replace(b'</ncx', b'</n:ncx')
        for name in (b'head', b'meta', b'docTitle', b'text', b'navMap', b'navPoint', b'navLabel', b'content'):
            data = data.replace(b'<' + name, b'<n:' + name).replace(b'</' + name, b'</n:' + name)
    elif formatting == 'double-quotes':
        data = data.replace(b"content='urn:example:wrong'", b'content = "urn:example:wrong"')
    else:
        data = data.replace(b'urn:example:wrong', b'urn:example:wr&#111;ng')
    changed = correct_ncx_identifier(data, CANONICAL)
    old = b'urn:example:wr&#111;ng' if formatting == 'entities' else b'urn:example:wrong'
    assert changed == data.replace(old, CANONICAL.encode())


def test_only_proven_attribute_changes_not_comment_or_text_with_same_value():
    with ZipFile(FIXTURES / 'identifier-mismatch.epub') as archive:
        data = archive.read(NCX)
    data = data.replace(b'<head>', b'<head><!-- urn:example:wrong -->')
    data = data.replace(b'<text>NCX test</text>', b'<text>urn:example:wrong</text>')
    changed = correct_ncx_identifier(data, CANONICAL)
    assert changed == data.replace(b"content='urn:example:wrong'", b"content='urn:example:canonical'")


@pytest.mark.parametrize('final,reason', [(False, 'revalidation_failed'),
    (EPUBCheckError('checker unavailable'), 'epubcheck_failure')])
def test_revalidation_failure_never_publishes_or_mutates(build, tmp_path, monkeypatch, final, reason):
    source = build()
    before = source.read_bytes()
    calls = stub(monkeypatch, final=final)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert len(calls) == 2 and result['reason'] == reason
    assert_refused(source, output, before, result)


def test_mutation_failure_reconfirmed_before_rebuild(build, tmp_path, monkeypatch):
    source = build()
    before = source.read_bytes()
    calls = stub(monkeypatch)
    real = diagnoser._ncx_identifier_replacement
    seen = []
    def changed_evidence(*args):
        seen.append(1)
        if len(seen) > 1:
            raise UnsafeZipError('unsafe_ncx_identifier_mutation', 'Proof changed')
        return real(*args)
    monkeypatch.setattr(diagnoser, '_ncx_identifier_replacement', changed_evidence)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'unsafe_ncx_identifier_mutation' and len(calls) == 1
    assert_refused(source, output, before, result)


def test_publication_copy_failure_removes_partial_output(build, tmp_path, monkeypatch):
    source = build()
    before = source.read_bytes()
    stub(monkeypatch)
    def fail_copy(source_file, output_file):
        output_file.write(b'partial')
        raise OSError('disk full')
    monkeypatch.setattr(repairer.shutil, 'copyfileobj', fail_copy)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'output_write_failed'
    assert_refused(source, output, before, result)


def test_existing_output_is_never_overwritten(build, tmp_path, monkeypatch):
    source = build()
    before = source.read_bytes()
    calls = stub(monkeypatch)
    output = tmp_path / 'out.epub'
    output.write_bytes(b'existing')
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'output_exists' and len(calls) == 1
    assert result['repaired'] == [] and source.read_bytes() == before
    assert output.read_bytes() == b'existing'


def test_unexpected_programming_error_is_not_hidden(build, tmp_path, monkeypatch):
    source = build()
    before = source.read_bytes()
    stub(monkeypatch)
    def bug(*args):
        raise TypeError('programming bug')
    monkeypatch.setattr(diagnoser, 'correct_ncx_identifier', bug)
    with pytest.raises(TypeError, match='programming bug'):
        repairer.repair_epub(str(source), str(tmp_path / 'out.epub'))
    assert source.read_bytes() == before and set(tmp_path.iterdir()) == {source}


def test_duplicate_validator_evidence_repairs_one_target_once(build, tmp_path, monkeypatch):
    source = build()
    initial = report()
    initial['messages'] *= 2
    stub(monkeypatch, initial)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['repaired'] == result['detected'] == [FAMILY]
    with ZipFile(output) as archive:
        assert archive.read(NCX).count(CANONICAL.encode()) == 1


@pytest.mark.parametrize('member', ['mimetype', NCX])
def test_zip_extras_stay_outside_safe_subset(member, build, tmp_path, monkeypatch):
    import struct
    source = build()
    with ZipFile(source) as archive:
        entries = [(copy(item), archive.read(item)) for item in archive.infolist()]
    with ZipFile(source, 'w') as archive:
        for item, data in entries:
            if item.filename == member:
                item.extra = struct.pack('<HHBI', 0x5455, 5, 1, 1700000000)
            archive.writestr(item, data)
    before = source.read_bytes()
    stub(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'unsafe_ncx_archive'
    assert_refused(source, output, before, result)


def test_real_epub3_backwards_compatible_ncx(make_epub, tmp_path):
    source = make_epub()
    with ZipFile(source) as archive:
        entries = [(copy(item), archive.read(item)) for item in archive.infolist()]
        package = archive.read(PACKAGE)
    canonical = ElementTree.fromstring(package).find(
        './{http://www.idpf.org/2007/opf}metadata/{http://purl.org/dc/elements/1.1/}identifier').text
    package = package.replace(b'<manifest>', b'<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    package = package.replace(b'<spine>', b'<spine toc="ncx">')
    with ZipFile(FIXTURES / 'identifier-mismatch.epub') as archive:
        ncx = archive.read(NCX)
    with ZipFile(source, 'w') as archive:
        for item, data in entries:
            archive.writestr(item, package if item.filename == PACKAGE else data)
        archive.writestr(NCX, ncx)
    before = source.read_bytes()
    initial = validate_epub(str(source))
    assert [message['ID'] for message in initial['messages']] == ['NCX-001']
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    assert result['repaired'] == [FAMILY] and result['final_valid'] is True
    checked = validate_epub(str(output))
    assert checked['fatal_count'] == checked['error_count'] == 0
    with ZipFile(output) as archive:
        assert canonical.encode() in archive.read(NCX)
    assert source.read_bytes() == before
