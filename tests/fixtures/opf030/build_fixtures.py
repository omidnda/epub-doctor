"""Self-authored deterministic fixtures; reuse neighboring fixture source constants."""
from pathlib import Path
import runpy
from zipfile import ZipFile, ZipInfo

ROOT = Path(__file__).parent
EPUB3 = runpy.run_path(str(ROOT.parent / 'rsc008/build_fixtures.py'))
EPUB2 = runpy.run_path(str(ROOT.parent / 'ncx001/build_fixtures.py'))
PACKAGE = EPUB3['PACKAGE'].replace('<dc:identifier id="other">urn:example:unrelated</dc:identifier>', '')
PACKAGE = PACKAGE.replace('unique-identifier="id"', "unique-identifier = 'absent'")
CANONICAL = 'urn:example:rsc008'


def build():
    identifier = '<dc:identifier id="id">' + CANONICAL + '</dc:identifier>'
    variants = {
        'unresolved': PACKAGE,
        'already-resolved': PACKAGE.replace("'absent'", "'id'"),
        'missing-reference': PACKAGE.replace(" unique-identifier = 'absent'", ''),
        'empty-reference': PACKAGE.replace("'absent'", "''"),
        'multiple-identifiers': PACKAGE.replace(identifier, identifier + '<dc:identifier id="other">urn:example:other</dc:identifier>'),
        'uuid-and-isbn': PACKAGE.replace(identifier, '<dc:identifier id="uuid-id">urn:uuid:ee0419e5-6ac0-4bf0-b7d7-1693c90b38a8</dc:identifier><dc:identifier id="isbn-id">9780000000002</dc:identifier>'),
        'identifier-without-id': PACKAGE.replace('dc:identifier id="id"', 'dc:identifier'),
        'invalid-id': PACKAGE.replace('dc:identifier id="id"', 'dc:identifier id="9invalid"'),
        'empty-identifier': PACKAGE.replace(CANONICAL, ''),
        'edge-whitespace': PACKAGE.replace(CANONICAL, ' ' + CANONICAL + ' '),
        'unrelated-error': PACKAGE,
        'invalid-uuid': PACKAGE.replace(CANONICAL, 'urn:uuid:not-a-uuid'),
        'ncx-matching': EPUB2['OPF'].replace('unique-identifier="book-id"', 'unique-identifier="absent"'),
        'ncx-mismatching': EPUB2['OPF'].replace('unique-identifier="book-id"', 'unique-identifier="absent"'),
    }
    for name, package in variants.items():
        files = {'mimetype': 'application/epub+zip', 'META-INF/container.xml': EPUB3['CONTAINER'],
                 'EPUB/package.opf': package, 'EPUB/nav.xhtml': EPUB3['NAV'],
                 'EPUB/chapter.xhtml': EPUB3['CHAPTER'].format(references='<p>Hello.</p>')}
        if name.startswith('ncx-'):
            files.pop('EPUB/nav.xhtml')
            files['EPUB/chapter.xhtml'] = EPUB2['CHAPTER']
            files['EPUB/toc.ncx'] = (EPUB2['NCX'].replace('urn:example:wrong', 'urn:example:canonical')
                                    if name == 'ncx-matching' else EPUB2['NCX'])
        if name == 'unrelated-error':
            files['EPUB/chapter.xhtml'] = files['EPUB/chapter.xhtml'].replace('<p>Hello.</p>', '<p><h1>Invalid nesting</h1></p>')
        with ZipFile(ROOT / (name + '.epub'), 'w') as archive:
            archive.comment = b'OPF-030 fixture comment'
            for path, text in files.items():
                entry = ZipInfo(path, (2020, 1, 1, 0, 0, 0))
                entry.comment = b'preserve member comment'
                archive.writestr(entry, text.encode('utf-8'))


if __name__ == '__main__':
    build()
