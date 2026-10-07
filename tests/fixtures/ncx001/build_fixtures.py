"""Reproduce small self-authored EPUB2 fixtures; no download or checker overrides."""
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

ROOT = Path(__file__).parent
OPF = ('<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="book-id">'
       '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
       '<dc:identifier id="book-id">urn:example:canonical</dc:identifier>'
       '<dc:title>NCX test</dc:title><dc:language>en</dc:language></metadata><manifest>'
       '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
       '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
       '</manifest><spine toc="ncx"><itemref idref="chapter"/></spine></package>')
NCX = ('<?xml version="1.0" encoding="UTF-8"?>\n'
       '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head>'
       '<!-- keep formatting and comments -->\n<meta name="dtb:uid" content=\'urn:example:wrong\'/>'
       '<meta name="dtb:depth" content="1"/><meta name="dtb:totalPageCount" content="0"/>'
       '<meta name="dtb:maxPageNumber" content="0"/></head><docTitle><text>NCX test</text></docTitle>'
       '<navMap><navPoint id="p1" playOrder="1"><navLabel><text>Chapter</text></navLabel>'
       '<content src="chapter.xhtml"/></navPoint></navMap></ncx>')
CONTAINER = ('<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
             '<rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>'
             '</rootfiles></container>')
CHAPTER = ('<?xml version="1.0"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
           '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd"><html xmlns="http://www.w3.org/1999/xhtml">'
           '<head><title>Chapter</title></head><body><p>Hello.</p></body></html>')


def build():
    uid = '<meta name="dtb:uid" content=\'urn:example:wrong\'/>'
    variants = {
        'identifier-mismatch': (OPF, NCX),
        'multiple-identifiers': (OPF.replace('<dc:identifier', '<dc:identifier id="other">urn:example:other</dc:identifier><dc:identifier', 1), NCX),
        'already-matching': (OPF, NCX.replace('urn:example:wrong', 'urn:example:canonical')),
        'missing-unique-identifier': (OPF.replace(' unique-identifier="book-id"', ''), NCX),
        'unresolved-identifier': (OPF.replace('unique-identifier="book-id"', 'unique-identifier="absent"'), NCX),
        'empty-identifier': (OPF.replace('urn:example:canonical', ''), NCX),
        'missing-ncx': (OPF, None),
        'multiple-ncx': (OPF.replace('</manifest>', '<item id="other-ncx" href="other.ncx" media-type="application/x-dtbncx+xml"/></manifest>'), NCX),
        'missing-uid': (OPF, NCX.replace(uid, '')),
        'duplicate-uid': (OPF, NCX.replace(uid, uid + uid)),
        'missing-content': (OPF, NCX.replace(" content='urn:example:wrong'", '')),
    }
    for name, (package, ncx) in variants.items():
        files = {'META-INF/container.xml': CONTAINER, 'EPUB/package.opf': package,
                 'EPUB/chapter.xhtml': CHAPTER}
        if ncx is not None:
            files['EPUB/toc.ncx'] = ncx
        if name == 'multiple-ncx':
            files['EPUB/other.ncx'] = NCX
        with ZipFile(ROOT / (name + '.epub'), 'w') as archive:
            archive.comment = b'NCX fixture archive comment'
            for path, data in [('mimetype', 'application/epub+zip'), *files.items()]:
                entry = ZipInfo(path, (2020, 1, 1, 0, 0, 0))
                entry.comment = b'preserve member comment'
                archive.writestr(entry, data.encode('utf-8'), compress_type=ZIP_STORED)


if __name__ == '__main__':
    build()
