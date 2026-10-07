import struct
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from src import repairer
from src.container_mutation import correct_rootfile_media_type
from src.validator import validate_epub
from src.zip_inspection import UnsafeZipError

BAD = b'application/oepbs-package+xml'
GOOD = b'application/oebps-package+xml'
DIAGNOSIS = 'rootfile_incorrect_media_type'
XML = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
       b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">\n'
       b'  <!-- media-type="application/oepbs-package+xml" -->\n'
       b'  <rootfiles>\n'
       b"    <rootfile full-path='EPUB/package.opf' media-type = 'application/oepbs-package+xml'/>\n"
       b'  </rootfiles>\n</container>\n')


@pytest.fixture
def source_factory(make_epub):
    def build(mode='valid', xml=XML, replacements=None, duplicate=None, extras=False):
        path = make_epub(mode)
        with ZipFile(path) as archive:
            entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
        replacements = {'META-INF/container.xml': xml, **(replacements or {})}
        with ZipFile(path, 'w') as archive:
            archive.comment = b'archive comment'
            for entry, data in entries:
                data = replacements.get(entry.filename, data)
                if data is None:
                    continue
                if entry.filename != 'mimetype':
                    entry.compress_type = ZIP_DEFLATED
                    entry.comment = b'entry comment'
                if extras and entry.filename == 'META-INF/container.xml':
                    entry.extra = struct.pack('<HH', 0xbeef, 0)
                archive.writestr(entry, data)
            for name, data in replacements.items():
                if name not in {entry.filename for entry, _ in entries} and data is not None:
                    archive.writestr(name, data)
            if duplicate:
                with pytest.warns(UserWarning):
                    archive.writestr(duplicate, XML if duplicate.endswith('.xml') else b'<package/>')
        return path
    return build


def fake_validation(monkeypatch, ids=('RSC-003',), final_valid=True, publication=None):
    calls = []
    def validate(path):
        calls.append(path)
        if len(calls) == 1:
            return {'valid': False, 'messages': [{'ID': code} for code in ids],
                    'publication': publication or {}}
        return {'valid': final_valid, 'messages': [] if final_valid else [{'ID': 'OTHER'}],
                'publication': {}}
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    return calls


def assert_refused(source, output, result):
    assert result['repaired'] == []
    assert result['output_file'] is None
    assert not output.exists()
    assert set(source.parent.iterdir()) == {source}


def assert_preserved(source, output, expected_xml, mimetype_repair=False):
    with ZipFile(source) as before, ZipFile(output) as after:
        assert before.comment == after.comment
        assert after.namelist()[0] == 'mimetype'
        assert after.read('mimetype') == b'application/epub+zip'
        assert after.getinfo('mimetype').compress_type == ZIP_STORED
        for entry in before.infolist():
            if mimetype_repair and entry.filename == 'mimetype':
                continue
            assert after.read(entry.filename) == (expected_xml if entry.filename == 'META-INF/container.xml'
                                                  else before.read(entry))
            copied = after.getinfo(entry.filename)
            for field in ('date_time', 'compress_type', 'comment', 'extra', 'internal_attr',
                          'external_attr', 'create_system', 'create_version', 'extract_version'):
                assert getattr(copied, field) == getattr(entry, field)


@pytest.mark.parametrize('bad', [BAD, b'text/plain'])
def test_real_repair(source_factory, tmp_path, bad):
    xml = XML.replace(b"media-type = '" + BAD, b"media-type = '" + bad)
    source = source_factory(xml=xml)
    original = source.read_bytes()
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == result['repaired'] == [DIAGNOSIS]
    assert result['original_valid'] is False and result['final_valid'] is True
    assert result['remaining_messages'] == [] and result['reason'] is None
    assert result['output_file'] == str(output)
    assert validate_epub(str(output))['valid'] is True
    assert source.read_bytes() == original
    expected = xml.replace(b"media-type = '" + bad, b"media-type = '" + GOOD)
    assert_preserved(source, output, expected)


@pytest.mark.parametrize('xml', [XML,
    XML.replace(b'<rootfiles>', b'<rootfiles><![CDATA[media-type="application/oepbs-package+xml"]]>'),
    XML.replace(b'<rootfile ', b'<ocf:rootfile xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" '),
    b'\xef\xbb\xbf' + XML,
    XML.replace(b"media-type = '" + BAD, b"media-type = 'application/&#111;epbs-package+xml"),
    XML.replace(b'<!-- media-type', '<!-- فارسی media-type'.encode()),
])
def test_minimal_mutation(xml):
    prefix = b"media-type = '"
    start = xml.index(prefix) + len(prefix)
    end = xml.index(b"'", start)
    assert correct_rootfile_media_type(xml) == xml[:start] + GOOD + xml[end:]


@pytest.mark.parametrize('xml', [XML.replace(b" media-type = '" + BAD + b"'", b''),
    XML.decode().replace('UTF-8', 'UTF-16').encode('utf-16'),
    XML.replace(b'UTF-8', b'ISO-8859-1'),
    b'<!DOCTYPE container>' + XML.split(b'\n', 1)[1],
    XML.replace(b'</rootfiles>', b'<rootfile media-type="wrong"/></rootfiles>'),
    b'<broken>', XML.replace(BAD, GOOD),
])
def test_unprovable_mutation_refused(xml):
    with pytest.raises(UnsafeZipError) as error:
        correct_rootfile_media_type(xml)
    assert error.value.reason == 'unsafe_container_mutation'


@pytest.mark.parametrize('xml', [XML.replace(b" media-type = '" + BAD + b"'", b''),
    XML.decode().replace('UTF-8', 'UTF-16').encode('utf-16'),
    XML.replace(b'UTF-8', b'ISO-8859-1')])
def test_confirmed_diagnosis_but_unsupported_mutation(monkeypatch, source_factory, tmp_path, xml):
    source = source_factory(xml=xml)
    calls = fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == [DIAGNOSIS]
    assert result['reason'] == 'unsafe_container_mutation'
    assert len(calls) == 1
    assert_refused(source, output, result)


@pytest.mark.parametrize('xml,replacements,duplicate', [
    (b'<broken>', {}, None), (None, {}, None),
    (XML.replace(b'</rootfiles>', b'<rootfile full-path="other"/></rootfiles>'), {}, None),
    (XML.replace(b" full-path='EPUB/package.opf'", b''), {}, None),
    (XML.replace(b'EPUB/package.opf', b''), {}, None),
    (XML, {'EPUB/package.opf': None}, None),
    (XML, {'EPUB/package.opf': b'<package xmlns="wrong"/>'}, None),
    (XML, {'EPUB/package.opf': b'<other xmlns="http://www.idpf.org/2007/opf"/>'}, None),
    (XML, {'EPUB/package.opf': b'<broken>'}, None),
    (XML.replace(b'EPUB/package.opf', b'../package.opf'), {'../package.opf': b'<package xmlns="http://www.idpf.org/2007/opf"/>'}, None),
    (XML.replace(BAD, GOOD), {}, None),
    (XML, {}, 'META-INF/container.xml'), (XML, {}, 'EPUB/package.opf'),
])
def test_diagnosis_refusals(monkeypatch, source_factory, tmp_path, xml, replacements, duplicate):
    source = source_factory(xml=xml, replacements=replacements, duplicate=duplicate)
    original = source.read_bytes()
    calls = fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert len(calls) == 1
    assert_refused(source, output, result)
    assert source.read_bytes() == original


@pytest.mark.parametrize('ids', [('RSC-003', 'OPF-016'), ('RSC-003', 'OPF-017'), ('OTHER',)])
def test_requires_diagnosis(monkeypatch, source_factory, tmp_path, ids):
    source = source_factory()
    fake_validation(monkeypatch, ids)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'no_supported_repair'
    assert_refused(source, output, result)


@pytest.mark.parametrize('protection', ['hasSignatures', 'hasEncryption', 'signatures', 'encryption', 'zip_container', 'zip_target'])
def test_protection(monkeypatch, source_factory, tmp_path, protection):
    replacements = {f'META-INF/{protection}.xml': b''} if protection in ('signatures', 'encryption') else {}
    source = source_factory(replacements=replacements)
    if protection.startswith('zip_'):
        name = 'META-INF/container.xml' if protection == 'zip_container' else 'EPUB/package.opf'
        with ZipFile(source) as archive:
            offset = archive.getinfo(name).header_offset
        data = bytearray(source.read_bytes())
        struct.pack_into('<H', data, offset + 6, 1)
        offset = data.index(b'PK\x01\x02')
        while True:
            name_len, extra_len, comment_len = struct.unpack_from('<HHH', data, offset + 28)
            if data[offset + 46:offset + 46 + name_len].decode() == name:
                struct.pack_into('<H', data, offset + 8, 1)
                break
            offset += 46 + name_len + extra_len + comment_len
        source.write_bytes(data)
    fake_validation(monkeypatch, publication={protection: True} if protection.startswith('has') else {})
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'signatures_or_encryption'
    assert_refused(source, output, result)


def test_revalidation_failure(monkeypatch, source_factory, tmp_path):
    source = source_factory()
    calls = fake_validation(monkeypatch, final_valid=False)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert len(calls) == 2
    assert result['reason'] == 'revalidation_failed'
    assert result['remaining_messages'] == [{'ID': 'OTHER'}]
    assert result['final_valid'] is False
    assert_refused(source, output, result)


def test_destination_guards(monkeypatch, source_factory, tmp_path):
    source = source_factory()
    original = source.read_bytes()
    fake_validation(monkeypatch)
    assert repairer.repair_epub(str(source), str(source))['reason'] == 'output_is_input'
    output = tmp_path / 'out.epub'
    output.write_bytes(b'keep')
    fake_validation(monkeypatch)
    assert repairer.repair_epub(str(source), str(output))['reason'] == 'output_exists'
    assert output.read_bytes() == b'keep'
    assert source.read_bytes() == original


@pytest.mark.parametrize('mode,expected', [('incorrect', ['mimetype_incorrect_content']),
    ('missing', ['mimetype_missing']), ('not_first', ['mimetype_not_first']),
    ('extra', ['mimetype_has_extra_field']),
    ('extra_incorrect_not_first', ['mimetype_not_first', 'mimetype_incorrect_content'])])
def test_real_combined_repair(monkeypatch, source_factory, tmp_path, mode, expected):
    source = source_factory(mode)
    original = source.read_bytes()
    calls = []
    def validate(path):
        calls.append(path)
        return validate_epub(path)
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == result['repaired'] == expected + [DIAGNOSIS]
    assert result['final_valid'] is True and result['reason'] is None
    assert len(calls) == 2
    assert source.read_bytes() == original
    assert_preserved(source, output, XML.replace(b"media-type = '" + BAD, b"media-type = '" + GOOD), True)


def test_extra_metadata_refused(monkeypatch, source_factory, tmp_path):
    source = source_factory(extras=True)
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'unsafe_metadata_preservation'
    assert_refused(source, output, result)


def test_zip64_refused(monkeypatch, source_factory, tmp_path):
    source = source_factory()
    with ZipFile(source, 'a') as archive:
        with archive.open(ZipInfo('extra.txt'), 'w', force_zip64=True) as stream:
            stream.write(b'data')
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'unsupported_zip64'
    assert_refused(source, output, result)


def test_mimetype_metadata_preserved(monkeypatch, source_factory, tmp_path):
    source = source_factory()
    with ZipFile(source) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
    with ZipFile(source, 'w') as archive:
        for entry, data in entries:
            if entry.filename == 'mimetype':
                entry.comment = b'mimetype comment'
                entry.date_time = (2001, 2, 3, 4, 5, 6)
            archive.writestr(entry, data)
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['repaired'] == [DIAGNOSIS]
    assert_preserved(source, output, XML.replace(b"media-type = '" + BAD, b"media-type = '" + GOOD))


@pytest.mark.parametrize('mode', ['missing', 'not_first', 'extra'])
def test_unconfirmed_mimetype_defect_not_silently_repaired(monkeypatch, source_factory, tmp_path, mode):
    source = source_factory(mode)
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'unsupported_mimetype_structure'
    assert_refused(source, output, result)


def test_local_header_conflict_refused(monkeypatch, source_factory, tmp_path):
    source = source_factory()
    with ZipFile(source) as archive:
        offset = archive.getinfo('EPUB/package.opf').header_offset
    data = bytearray(source.read_bytes())
    struct.pack_into('<H', data, offset + 8, ZIP_STORED)
    source.write_bytes(data)
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['repaired'] == [] and result['output_file'] is None
    assert_refused(source, output, result)


def test_metadata_normalization_refused(monkeypatch, source_factory, tmp_path):
    source = source_factory()
    with ZipFile(source) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
    with ZipFile(source, 'w') as archive:
        for entry, data in entries:
            if entry.filename == 'EPUB/package.opf':
                entry.compress_type = ZIP_BZIP2
            archive.writestr(entry, data)
    original_infolist = ZipFile.infolist
    def changed_metadata(archive):
        entries = original_infolist(archive)
        if str(archive.filename) == str(source):
            for entry in entries:
                if entry.filename == 'EPUB/package.opf':
                    entry.create_version = 1
        return entries
    monkeypatch.setattr(ZipFile, 'infolist', changed_metadata)
    fake_validation(monkeypatch)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['reason'] == 'unsafe_metadata_preservation'
    assert_refused(source, output, result)


def test_all_reported_repairs_share_one_rebuild(monkeypatch, source_factory, tmp_path):
    source = source_factory('extra_incorrect_not_first')
    calls = []
    def validate(path):
        calls.append(path)
        if len(calls) == 1:
            return {'valid': False, 'publication': {}, 'messages': [{'ID': code} for code in
                    ('PKG-005', 'PKG-006', 'PKG-007', 'RSC-003')]}
        return validate_epub(path)
    monkeypatch.setattr(repairer, 'validate_epub', validate)
    output = tmp_path / 'out.epub'
    result = repairer.repair_epub(str(source), str(output))
    assert result['detected'] == result['repaired'] == [
        'mimetype_not_first', 'mimetype_incorrect_content', 'mimetype_has_extra_field', DIAGNOSIS]
    assert len(calls) == 2 and result['final_valid'] is True
