import struct
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from src import diagnoser, repairer, service
from src.manifest_mutation import add_required_manifest_properties
from src.validator import validate_epub
from src.zip_inspection import UnsafeZipError


FIXTURES = Path(__file__).parent / 'fixtures/opf014'
DIAGNOSIS = 'manifest_missing_required_property'
PACKAGE = 'EPUB/package.opf'
OPF = '{http://www.idpf.org/2007/opf}'
METADATA = ('date_time', 'compress_type', 'comment', 'extra', 'internal_attr',
            'external_attr', 'create_system', 'create_version', 'extract_version')


def message(token='scripted', paths=('EPUB/chapter.xhtml',)):
    return {'ID': 'OPF-014', 'message': f'The property "{token}" should be declared in the OPF file.',
            'locations': [{'path': path} for path in paths]}


def report(messages=None):
    return {'valid': False, 'messages': [message()] if messages is None else messages,
            'publication': {}}


def xml(items=b'<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
        attributes=b''):
    return (b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" ' + attributes + b'>\n'
            b'<!-- unrelated properties="scripted" -->\n<metadata/>\n<manifest>\n' + items +
            b'\n</manifest>\n<spine><itemref idref="chapter"/></spine>\n</package>')


@pytest.fixture
def build(make_epub):
    def factory(replacements=None, mode='valid'):
        source = make_epub(mode)
        with ZipFile(source) as archive:
            entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
        with ZipFile(source, 'w') as archive:
            archive.comment = b'archive comment'
            seen = set()
            for entry, data in entries:
                seen.add(entry.filename)
                data = (replacements or {}).get(entry.filename, data)
                if data is not None:
                    entry.comment = b'entry comment'
                    archive.writestr(entry, data)
            for name, data in (replacements or {}).items():
                if name not in seen and data is not None:
                    archive.writestr(name, data)
        return source
    return factory


def stub(monkeypatch, validation=None, final_valid=True):
    calls = []
    def validate(path):
        calls.append(path)
        return (validation or report()) if len(calls) == 1 else {
            'valid': final_valid, 'messages': [] if final_valid else [{'ID': 'OTHER'}], 'publication': {}}
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    return calls


def assert_refused(source, output, result, before):
    assert result['repaired'] == []
    assert result['output_file'] is None
    assert not output.exists()
    assert source.read_bytes() == before
    assert set(source.parent.iterdir()) == {source}


@pytest.mark.parametrize('fixture', sorted(path.name for path in FIXTURES.glob('*.epub')))
def test_official_real_epubcheck_repair(fixture, tmp_path):
    source = FIXTURES / fixture
    original = source.read_bytes()
    before = validate_epub(str(source))
    assert (before['fatal_count'], before['error_count'], before['warning_count']) == (0, 1, 0)
    targets = [msg for msg in before['messages'] if msg['ID'] == 'OPF-014']
    assert targets
    assert diagnoser.diagnose_epub(str(source), before) == [DIAGNOSIS]
    output = tmp_path / 'out.epub'
    result = service.repair_epub_file(str(source), str(output))
    after = validate_epub(str(output))
    assert result == dict(original_valid=False, detected=[DIAGNOSIS], repaired=[DIAGNOSIS],
                          final_valid=True, output_file=str(output), remaining_messages=after['messages'], reason=None)
    assert after['valid'] is True
    assert after['fatal_count'] == after['error_count'] == 0
    # Bundled EPUBCheck 5.4.0 reports OPF-018 after the inline-CSS declaration.
    # Preserve the existing engine policy: warnings do not make an EPUB invalid.
    if fixture == 'package-remote-font-in-inline-css-missing-property-error.epub':
        assert after['warning_count'] == 1
        assert [(msg['ID'], msg['severity']) for msg in after['messages']] == [('OPF-018', 'WARNING')]
    else:
        assert after['warning_count'] == 0
    assert source.read_bytes() == original
    with ZipFile(source) as archive, ZipFile(output) as repaired:
        assert repaired.namelist() == archive.namelist()
        assert repaired.comment == archive.comment
        expected = add_required_manifest_properties(archive.read(PACKAGE), PACKAGE,
                                                    before['messages'], set(archive.namelist()))
        assert repaired.read(PACKAGE) == expected
        assert repaired.read(PACKAGE) != archive.read(PACKAGE)
        for entry in archive.infolist():
            if entry.filename != PACKAGE:
                assert repaired.read(entry.filename) == archive.read(entry)
            assert all(getattr(repaired.getinfo(entry.filename), field) == getattr(entry, field)
                       for field in METADATA)
        root = ElementTree.fromstring(repaired.read(PACKAGE))
        for msg in targets:
            token = msg['message'].split('"')[1]
            for location in msg['locations']:
                matching = [item for item in root.find(OPF + 'manifest')
                            if 'EPUB/' + item.get('href', '') == location['path']]
                assert len(matching) == 1
                assert matching[0].get('properties', '').split().count(token) == 1


@pytest.mark.parametrize('bad_message', [
    message('switch'), message('future-token'),
    {**message(), 'message': 'Please declare scripted'},
    {**message(), 'message': message()['message'] + ' '},
    {**message(), 'message': None}, {**message(), 'message': []},
    {**message(), 'locations': []}, {**message(), 'locations': None},
    {key: value for key, value in message().items() if key != 'locations'},
    {**message(), 'locations': [{}]}, {**message(), 'locations': [None]},
    {**message(), 'additionalLocations': 1},
])
def test_unsupported_evidence_no_partial_repair(build, monkeypatch, tmp_path, bad_message):
    source = build()
    before = source.read_bytes()
    validation = report([message(), bad_message])
    assert diagnoser.diagnose_epub(str(source), validation) == []
    stub(monkeypatch, validation)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'no_supported_repair'
    assert_refused(source, output, result, before)


@pytest.mark.parametrize('path', ['', '../EPUB/chapter.xhtml', '/EPUB/chapter.xhtml',
    'EPUB/../EPUB/chapter.xhtml', 'EPUB/./chapter.xhtml', 'EPUB//chapter.xhtml',
    'EPUB/chapter.xhtml#id', 'EPUB/chapter.xhtml?q=1', 'EPUB/chapter%2exhtml',
    'https://example.org/chapter.xhtml', 'EPUB\\chapter.xhtml', 'EPUB/chapter.xhtml\x00',
    'EPUB/chapter.xhtml\x85', 'EPUB/no-match.xhtml', 'EPUB/chapter xhtml', None, 42])
def test_unsafe_or_unmapped_location(build, path):
    source = build()
    assert diagnoser.diagnose_epub(str(source), report([message(paths=('EPUB/chapter.xhtml', path))])) == []


@pytest.mark.parametrize('items', [
    b'<item id="a" href="chapter.xhtml"/><item id="b" href="./chapter.xhtml"/>',
    b'<item id="a" href="chapter.xhtml"/><item id="b" href="../EPUB/chapter.xhtml"/>',
    b'<item id="a" href="chapter.xhtml"/><item id="a" href="nav.xhtml"/>',
    b'<item href="chapter.xhtml"/>',
    b'<item id="a" href="chapter.xhtml" properties="mathml scripted"/>',
    b'<item id="a" href="chapter.xhtml" properties="scripted scripted"/>',
    b'<item id="a" href="chapter.xhtml" properties="&#115;cripted"/>',
    b'<item id="a" href="missing.xhtml"/>',
    b'<item id="a" href="chapter.xhtml" xml:base="other/"/>',
    b'<item id="a" href="chapter.xhtml"/><item id="b" href="chapter%2exhtml"/>',
])
def test_inconsistent_or_ambiguous_manifest(build, items):
    source = build({PACKAGE: xml(items)})
    assert diagnoser.diagnose_epub(str(source), report()) == []


@pytest.mark.parametrize('href,path', [('chapter.xhtml', 'EPUB/chapter.xhtml'),
    ('./chapter.xhtml', 'EPUB/chapter.xhtml'), ('../EPUB/chapter.xhtml', 'EPUB/chapter.xhtml'),
    ('sub/../chapter.xhtml', 'EPUB/chapter.xhtml'), ('../chapter.xhtml', 'chapter.xhtml')])
def test_normal_relative_uri_resolution(href, path):
    data = xml(f'<item id="chapter" href="{href}"/>'.encode())
    changed = add_required_manifest_properties(data, PACKAGE, [message(paths=(path,))], {path})
    assert b'properties="scripted"' in changed


@pytest.mark.parametrize('href', ['../../chapter.xhtml', '/EPUB/chapter.xhtml',
    '//example.org/chapter.xhtml', 'chapter.xhtml#fragment', 'chapter.xhtml?q=1',
    'chapter%2exhtml', 'sub//chapter.xhtml', 'chapter\\xhtml', 'chapter.xhtml/..',
    './', 'ftp://example.org/chapter.xhtml', 'javascript:alert(1)', 'chapter xhtml'])
def test_unsafe_manifest_uri(href):
    data = xml(f'<item id="chapter" href="{href}"/>'.encode())
    with pytest.raises(UnsafeZipError, match='manifest|Manifest|URI|resource'):
        add_required_manifest_properties(data, PACKAGE, [message()], {'EPUB/chapter.xhtml'})


@pytest.mark.parametrize('data', [
    b'<broken>', b'<!DOCTYPE package><package/>',
    b'<!DOCTYPE package [<!ENTITY x "scripted">]>' + xml().split(b'\n', 1)[1],
    xml(attributes=b'xml:base="other/"'),
    xml().replace(b'<manifest>', b'<manifest xml:base="other/">'),
    xml().replace(b'</manifest>', b'</manifest><manifest/>'),
    xml().replace(b'version="3.0"', b'version="2.0"'),
    xml().replace(b'http://www.idpf.org/2007/opf', b'urn:wrong'),
    xml().replace(b'encoding="UTF-8"', b'encoding="ISO-8859-1"'),
    xml().decode().encode('utf-16'),
    b' ' * (8 * 1024 * 1024 + 1),
], ids=['malformed', 'doctype', 'entity', 'package-base', 'manifest-base',
        'two-manifests', 'epub2', 'wrong-namespace', 'latin1-declaration', 'utf16', 'oversized'])
def test_unsafe_opf(build, data):
    source = build({PACKAGE: data})
    assert diagnoser.diagnose_epub(str(source), report()) == []


@pytest.mark.parametrize('replacement', [None, b'<broken>',
    b'<!DOCTYPE container [<!ENTITY x "bad">]><container/>',
    b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    b'<rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>'
    b'<rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>'
    b'</rootfiles></container>'])
def test_bad_container(build, replacement):
    source = build({'META-INF/container.xml': replacement})
    assert diagnoser.diagnose_epub(str(source), report()) == []


@pytest.mark.parametrize('extra', ['META-INF/signatures.xml', 'META-INF/encryption.xml'])
def test_archive_protection(build, extra, monkeypatch, tmp_path):
    source = build({extra: b''})
    before = source.read_bytes()
    assert diagnoser.diagnose_epub(str(source), report()) == []
    stub(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'signatures_or_encryption'
    assert_refused(source, output, result, before)


@pytest.mark.parametrize('flag', ['hasEncryption', 'hasSignatures'])
def test_publication_protection(build, flag):
    validation = report()
    validation['publication'][flag] = True
    assert diagnoser.diagnose_epub(str(build()), validation) == []


@pytest.mark.parametrize('name', [PACKAGE, 'mimetype', 'EPUB/chapter.xhtml'])
def test_duplicate_entries(build, name, monkeypatch, tmp_path):
    source = build()
    with ZipFile(source, 'a') as archive, pytest.warns(UserWarning):
        archive.writestr(name, b'duplicate')
    before = source.read_bytes()
    assert diagnoser.diagnose_epub(str(source), report()) == []
    stub(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'duplicate_zip_entries'
    assert_refused(source, output, result, before)


@pytest.mark.parametrize('mutation', ['encrypted', 'descriptor', 'crc', 'zip64', 'extra'])
def test_unsafe_archive_structure(build, mutation):
    source = build()
    with ZipFile(source) as archive:
        target = archive.getinfo(PACKAGE)
        offset = target.header_offset
        central = archive.start_dir
        index = archive.namelist().index(PACKAGE)
    raw = bytearray(source.read_bytes())
    # Find this central header by its known entry index.
    cursor = central
    for _ in range(index):
        name, extra, comment = struct.unpack_from('<3H', raw, cursor + 28)
        cursor += 46 + name + extra + comment
    if mutation in ('encrypted', 'descriptor'):
        flag = 1 if mutation == 'encrypted' else 8
        struct.pack_into('<H', raw, offset + 6, flag)
        struct.pack_into('<H', raw, cursor + 8, flag)
    elif mutation == 'crc':
        struct.pack_into('<I', raw, offset + 14, 123)
    elif mutation == 'zip64':
        struct.pack_into('<H', raw, offset + 4, 45)
        struct.pack_into('<H', raw, cursor + 6, 45)
    else:
        with ZipFile(source) as archive:
            entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
        with ZipFile(source, 'w') as archive:
            for entry, data in entries:
                if entry.filename == PACKAGE:
                    entry.extra = struct.pack('<HH', 0xbeef, 0)
                archive.writestr(entry, data)
        raw = source.read_bytes()
    source.write_bytes(raw)
    assert diagnoser.diagnose_epub(str(source), report()) == []


@pytest.mark.parametrize('properties', [None, b'nav mathml', b'', b' mathml  ', b'mathml&#32;nav'])
@pytest.mark.parametrize('quote', [b'"', b"'"])
def test_byte_local_tokens_preserved(properties, quote):
    item = b'<item id="chapter" href="chapter.xhtml"'
    if properties is not None:
        item += b' properties = ' + quote + properties + quote
    item += b' data-unrelated="preserve" />'
    data = xml(item)
    changed = add_required_manifest_properties(data, PACKAGE, [message()], {'EPUB/chapter.xhtml'})
    suffix = b'scripted'
    if properties is None:
        expected = data.replace(b' />', b'  properties="scripted"/>', 1)
    else:
        expected = data.replace(quote + properties + quote,
                                quote + properties + (b' ' if properties else b'') + suffix + quote, 1)
    assert changed == expected
    before_item = ElementTree.fromstring(data).find(OPF + 'manifest')[0]
    after_item = ElementTree.fromstring(changed).find(OPF + 'manifest')[0]
    assert after_item.get('properties').split().count('scripted') == 1
    assert set(before_item.get('properties', '').split()) <= set(after_item.get('properties').split())
    assert {k: v for k, v in after_item.attrib.items() if k != 'properties'} == {
        k: v for k, v in before_item.attrib.items() if k != 'properties'}


def test_multiple_messages_and_locations_coalesced():
    data = xml(b'<item id="chapter" href="chapter.xhtml"/>'
               b'<item id="nav" href="nav.xhtml" properties="nav"/>')
    messages = [message('svg', ('EPUB/chapter.xhtml', 'EPUB/nav.xhtml')),
                message('scripted'), message('svg'), message('remote-resources')]
    changed = add_required_manifest_properties(data, PACKAGE, messages,
                                               {'EPUB/chapter.xhtml', 'EPUB/nav.xhtml'})
    items = ElementTree.fromstring(changed).find(OPF + 'manifest')
    assert items[0].get('properties') == 'remote-resources scripted svg'
    assert items[1].get('properties') == 'nav svg'


def test_only_reported_properties_are_added():
    changed = add_required_manifest_properties(xml(), PACKAGE, [message('svg')], {'EPUB/chapter.xhtml'})
    assert b'properties="svg"' in changed
    assert b'properties="scripted"' not in changed.replace(b'<!-- unrelated properties="scripted" -->', b'')
    with pytest.raises(UnsafeZipError, match='No OPF-014'):
        add_required_manifest_properties(xml(), PACKAGE, [], {'EPUB/chapter.xhtml'})


def test_prefix_bom_comments_and_quoted_delimiters():
    data = b'\xef\xbb\xbf' + xml(b'<p:item id="chapter" href="chapter.xhtml" data-attr="a &gt; b"/>').replace(
        b'<package ', b'<package xmlns:p="http://www.idpf.org/2007/opf" ', 1)
    changed = add_required_manifest_properties(data, PACKAGE, [message()], {'EPUB/chapter.xhtml'})
    assert changed == data.replace(b'data-attr="a &gt; b"/>',
                                   b'data-attr="a &gt; b" properties="scripted"/>')


def test_no_resource_content_detection(build, monkeypatch):
    source = build()
    original_read, original_open = ZipFile.read, ZipFile.open
    def read(archive, name, *args, **kwargs):
        assert (name if isinstance(name, str) else name.filename) in ('mimetype', 'META-INF/container.xml', PACKAGE)
        return original_read(archive, name, *args, **kwargs)
    def open_entry(archive, name, *args, **kwargs):
        assert (name if isinstance(name, str) else name.filename) in ('mimetype', 'META-INF/container.xml', PACKAGE)
        return original_open(archive, name, *args, **kwargs)
    monkeypatch.setattr(ZipFile, 'read', read)
    monkeypatch.setattr(ZipFile, 'open', open_entry)
    assert diagnoser.diagnose_epub(str(source), report()) == [DIAGNOSIS]
    assert diagnoser.diagnose_epub(str(source), report([])) == []


def test_output_collision_and_same_path(build, monkeypatch, tmp_path):
    source = build()
    before = source.read_bytes()
    stub(monkeypatch)
    assert repairer.repair_epub(str(source), str(source))['reason'] == 'output_is_input'
    stub(monkeypatch)
    output = tmp_path / 'out.epub'
    output.write_bytes(b'keep')
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'output_exists'
    assert result['repaired'] == []
    assert output.read_bytes() == b'keep'
    assert source.read_bytes() == before


def test_failed_revalidation_never_publishes(build, monkeypatch, tmp_path):
    source = build()
    before = source.read_bytes()
    stub(monkeypatch, final_valid=False)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == [DIAGNOSIS]
    assert result['reason'] == 'revalidation_failed'
    assert result['final_valid'] is False
    assert result['remaining_messages'] == [{'ID': 'OTHER'}]
    assert_refused(source, output, result, before)


def test_repair_reconfirms_evidence(build, monkeypatch, tmp_path):
    source = build()
    before = source.read_bytes()
    real = diagnoser._manifest_property_replacement
    calls = []
    def replacement(archive, validation):
        calls.append(1)
        return real(archive, validation) if len(calls) == 1 else None
    monkeypatch.setattr(diagnoser, '_manifest_property_replacement', replacement)
    stub(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'unsafe_manifest_property_repair'
    assert_refused(source, output, result, before)


@pytest.mark.parametrize('replacements', [
    {PACKAGE: None},
    {'META-INF/container.xml': b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="../EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'},
    {'META-INF/container.xml': b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xml:base="other/"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'},
    {'META-INF/container.xml': b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="text/plain"/></rootfiles></container>'},
    {'EPUB/chapter.xhtml': None},
    {'EPUB/chapter.xhtml': None, 'EPUB/chapter.xhtml/': b''},
    {'mimetype': b'application/epub+zip' + b' ' * 100},
], ids=['missing-package', 'container-traversal', 'container-base', 'wrong-media-type',
        'missing-resource', 'directory-resource', 'oversized-mimetype'])
def test_package_and_resource_preconditions(build, replacements):
    assert diagnoser.diagnose_epub(str(build(replacements)), report()) == []


@pytest.mark.parametrize('mode', ['missing', 'incorrect', 'not_first', 'extra'])
def test_opf_subset_requires_canonical_mimetype(build, mode):
    assert diagnoser.diagnose_epub(str(build(mode=mode)), report()) == []


def test_same_item_multiple_messages_real_rebuild(build, monkeypatch, tmp_path):
    source = build({PACKAGE: xml()})
    validation = report([message('svg'), message('scripted'), message('svg')])
    stub(monkeypatch, validation)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == result['repaired'] == [DIAGNOSIS]
    with ZipFile(output) as archive:
        item = ElementTree.fromstring(archive.read(PACKAGE)).find(OPF + 'manifest')[0]
        assert item.get('properties') == 'scripted svg'


def test_mutation_verification_refuses_unrelated_semantic_change(monkeypatch):
    from src import manifest_mutation
    original = manifest_mutation._parse
    calls = []
    def parse(data):
        events, items = original(data)
        calls.append(1)
        if len(calls) == 2:
            events.append(('comment', 'unexpected'))
        return events, items
    monkeypatch.setattr(manifest_mutation, '_parse', parse)
    with pytest.raises(UnsafeZipError, match='Unexpected OPF semantic change'):
        add_required_manifest_properties(xml(), PACKAGE, [message()], {'EPUB/chapter.xhtml'})


def test_official_switch_refused(tmp_path):
    source = FIXTURES / 'refused/package-manifest-prop-switch-not-declared-error.epub'
    before = source.read_bytes()
    validation = validate_epub(str(source))
    assert {'OPF-014', 'RSC-017'} <= {msg['ID'] for msg in validation['messages']}
    assert diagnoser.diagnose_epub(str(source), validation) == []
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'no_supported_repair'
    assert result['detected'] == result['repaired'] == []
    assert result['output_file'] is None
    assert not output.exists()
    assert source.read_bytes() == before
