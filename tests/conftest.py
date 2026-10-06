from zipfile import ZIP_STORED, ZipFile

import pytest


@pytest.fixture
def make_epub(tmp_path):
    def make(mode="valid", extra=None):
        path = tmp_path / f"{mode}.epub"
        files = {
            "META-INF/container.xml": '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>''',
            "EPUB/package.opf": '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:uuid:ee0419e5-6ac0-4bf0-b7d7-1693c90b38a8</dc:identifier><dc:title>Test Book</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>''',
            "EPUB/nav.xhtml": '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Chapter</a></li></ol></nav></body></html>''',
            "EPUB/chapter.xhtml": '''<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head><body><h1>Chapter</h1><p>Hello.</p></body></html>''',
        }
        with ZipFile(path, "w") as archive:
            if mode == "valid":
                archive.writestr("mimetype", b"application/epub+zip", compress_type=ZIP_STORED)
            for name, data in files.items():
                archive.writestr(name, data)
            if mode == "not_first":
                archive.writestr("mimetype", b"application/epub+zip", compress_type=ZIP_STORED)
            for name, data in (extra or {}).items():
                archive.writestr(name, data)
        return path
    return make
