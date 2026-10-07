import struct
from xml.sax.saxutils import quoteattr
from zipfile import ZipFile

import pytest

from src.diagnoser import diagnose_epub
from src.validator import validate_epub

OCF = 'urn:oasis:names:tc:opendocument:xmlns:container'
OPF = '<package xmlns="http://www.idpf.org/2007/opf" version="2.0"/>'
EXPECTED = ['rootfile_incorrect_media_type']


def container(path='OPS/package.opf', media='application/oepbs-package+xml'):
    attrs = '' if path is None else ' full-path=' + quoteattr(path)
    if media is not None:
        attrs += ' media-type=' + quoteattr(media)
    return f'<container xmlns="{OCF}" version="1.0"><rootfiles><rootfile{attrs}/></rootfiles></container>'


@pytest.fixture
def build(tmp_path):
    def make(xml=None, target=OPF, name='OPS/package.opf'):
        path = tmp_path / 'test.epub'
        with ZipFile(path, 'w') as archive:
            if xml is not None:
                archive.writestr('META-INF/container.xml', xml)
            if target is not None:
                archive.writestr(name, target)
        return path
    return make


def diagnose(path, ids=('RSC-003',)):
    return diagnose_epub(str(path), {'messages': [{'ID': code} for code in ids]})


def test_wrong_media_type(build):
    path = build(container())
    original = path.read_bytes()
    assert diagnose(path) == EXPECTED
    assert path.read_bytes() == original


@pytest.mark.parametrize('ids', [(), ('PKG-003',), ('RSC-003', 'OPF-016'), ('RSC-003', 'OPF-017')])
def test_message_gates(build, ids):
    assert diagnose(build(container()), ids) == []


@pytest.mark.parametrize('xml', [None, '<broken>', container(None), container(''),
    container(media='application/oebps-package+xml'),
    container().replace(OCF, 'wrong'),
    container().replace('container', 'other'),
    container().replace('</rootfiles>', '<rootfile full-path="other"/></rootfiles>'),
    container().replace('</container>', '<rootfiles/></container>'),
    container().replace('<rootfiles>', '<wrapper><rootfiles>').replace('</rootfiles>', '</rootfiles></wrapper>'),
    container().replace('</container>', '<rootfile full-path="other"/></container>'),
    '<!DOCTYPE container [<!ENTITY value "OPS/package.opf">]>' + container('&value;'),
])
def test_invalid_or_ambiguous_container(build, xml):
    assert diagnose(build(xml)) == []


@pytest.mark.parametrize('target', [None, '<broken>', '<html/>', '<package/>',
    '<package xmlns="wrong"/>', '<other xmlns="http://www.idpf.org/2007/opf"/>',
    '<!DOCTYPE package SYSTEM "file:///etc/passwd">' + OPF])
def test_invalid_target(build, target):
    assert diagnose(build(container(), target)) == []


@pytest.mark.parametrize('name', ['META-INF/container.xml', 'OPS/package.opf'])
def test_duplicates(build, name):
    path = build(container())
    with pytest.warns(UserWarning), ZipFile(path, 'a') as archive:
        archive.writestr(name, container() if name.endswith('.xml') else OPF)
    assert diagnose(path) == []


@pytest.mark.parametrize('name', ['META-INF/container.xml', 'OPS/package.opf'])
def test_encrypted_flags(build, name):
    path = build(container())
    with ZipFile(path) as archive:
        offset = archive.getinfo(name).header_offset
    data = bytearray(path.read_bytes())
    struct.pack_into('<H', data, offset + 6, 1)
    offset = data.index(b'PK\x01\x02')
    while True:
        name_len, extra_len, comment_len = struct.unpack_from('<HHH', data, offset + 28)
        if data[offset + 46:offset + 46 + name_len].decode() == name:
            struct.pack_into('<H', data, offset + 8, 1)
            break
        offset += 46 + name_len + extra_len + comment_len
    path.write_bytes(data)
    assert diagnose(path) == []


@pytest.mark.parametrize('name', ['../package.opf', '/OPS/package.opf', 'OPS/../package.opf',
    'OPS/./package.opf', 'OPS//package.opf', 'OPS\\package.opf', 'C:/package.opf',
    'OPS/%70ackage.opf', 'OPS/package.opf#x', 'OPS/package.opf?x', ' OPS/package.opf',
    'OPS/package.opf ', 'OPS/package.opf/', 'https://example.com/package.opf'])
def test_suspicious_paths_even_when_entry_exists(build, name):
    assert diagnose(build(container(name), name=name)) == []


def test_extension_is_not_required(build):
    assert diagnose(build(container('OPS/package.xml'), name='OPS/package.xml')) == EXPECTED


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
def test_dtd_rejected_in_both_encodings(build, encoding):
    xml = ('<!DOCTYPE container [<!ENTITY path "OPS/package.opf">]>'
           + container().replace('OPS/package.opf', '&path;')).encode(encoding)
    assert diagnose(build(xml)) == []


def test_bad_zip_refused(tmp_path):
    path = tmp_path / 'broken.epub'
    path.write_bytes(b'not zip')
    assert diagnose(path) == []


def test_real_epubcheck_wrong_media_type(make_epub):
    path = make_epub()
    with ZipFile(path) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
    with ZipFile(path, 'w') as archive:
        for entry, data in entries:
            if entry.filename == 'META-INF/container.xml':
                data = data.replace(b'application/oebps-package+xml', b'application/oepbs-package+xml')
            archive.writestr(entry, data)
    report = validate_epub(str(path))
    assert {message['ID'] for message in report['messages']} == {'RSC-003'}
    assert diagnose_epub(str(path), report) == EXPECTED


def test_combined_pkg_diagnosis(build):
    assert diagnose(build(container()), ('RSC-003', 'PKG-006')) == ['mimetype_missing', *EXPECTED]


def test_oversized_xml_refused(build):
    assert diagnose(build(container() + ' ' * (8 * 1024 * 1024))) == []
