"""Reproducible self-authored EPUB3 fixtures; standard library only.

The three embedded base64 assets are self-authored 1x1 images, initially
encoded with Pillow 12.1.1. Rebuilding/testing requires no Pillow dependency.
"""
import base64
from pathlib import Path
from zipfile import ZipFile, ZipInfo

ROOT = Path(__file__).parent
PACKAGE = ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="id">urn:example:rsc008</dc:identifier>'
           '<dc:identifier id="other">urn:example:unrelated</dc:identifier>'
           '<dc:title>Resource fixture</dc:title><dc:language>en</dc:language>'
           '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata>'
           '<manifest><!-- preserve this manifest comment -->\n'
           '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>\n'
           '</manifest><spine><itemref idref="chapter"/></spine></package>')
CONTAINER = ('<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
             '<rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>'
             '</rootfiles></container>')
NAV = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
       '<head><title>Contents</title></head><body><nav epub:type="toc"><ol>'
       '<li><a href="chapter.xhtml">Chapter</a></li></ol></nav></body></html>')
CHAPTER = ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head>'
           '<body><h1>Chapter</h1>{references}</body></html>')


def build():
    assets = {extension: base64.b64decode((ROOT / (kind + '.b64')).read_text())
              for extension, kind in [('png', 'png'), ('jpg', 'jpeg'), ('gif', 'gif')]}
    assets['svg'] = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1"/></svg>'
    assets['css'] = b'body { color: black; }'
    assets['xhtml'] = CHAPTER.format(references='<p>Linked chapter.</p>').encode()
    variants = {
        'png': ['png'], 'jpeg': ['jpg'], 'multiple': ['png', 'jpg', 'gif'],
        'svg': ['svg'], 'css': ['css'], 'xhtml': ['xhtml'], 'unknown': ['bin'],
        'missing': ['png'], 'scripted-xhtml': ['xhtml'], 'unrelated-error': ['png'],
        'already-declared': ['png'], 'linked-xhtml': ['xhtml'],
    }
    for name, extensions in variants.items():
        references, resources = [], {}
        for extension in extensions:
            href = 'images/resource.' + extension
            if extension == 'css':
                references.append('<link xmlns="http://www.w3.org/1999/xhtml" rel="stylesheet" href="' + href + '"/>')
            elif extension == 'xhtml':
                references.append('<a href="' + href + '">Linked chapter</a>' if name == 'linked-xhtml'
                                  else '<iframe src="' + href + '" title="Embedded chapter"></iframe>')
            else:
                references.append('<img src="' + href + '" alt="pixel"/>')
            if name != 'missing':
                resources['EPUB/' + href] = assets.get(extension, b'unknown-format')
        chapter = CHAPTER.format(references=''.join(references))
        if name == 'css':
            chapter = CHAPTER.format(references='<p>Styled chapter.</p>').replace('</head>', references[0] + '</head>')
        if name == 'scripted-xhtml':
            resources['EPUB/images/resource.xhtml'] = assets['xhtml'].replace(b'</head>', b'<script>var a = 1;</script></head>')
        if name == 'unrelated-error':
            chapter = chapter.replace('<h1>', '<p><h1>').replace('</h1>', '</h1></p>')
        package = PACKAGE
        if name == 'already-declared':
            package = package.replace('</manifest>', '<item id="image" href="images/resource.png" media-type="image/png"/></manifest>')
        files = {'mimetype': b'application/epub+zip', 'META-INF/container.xml': CONTAINER.encode(),
                 'EPUB/package.opf': package.encode(), 'EPUB/nav.xhtml': NAV.encode(),
                 'EPUB/chapter.xhtml': chapter.encode(), **resources}
        with ZipFile(ROOT / (name + '.epub'), 'w') as archive:
            archive.comment = b'RSC-008 archive comment'
            for path, data in files.items():
                entry = ZipInfo(path, (2020, 1, 1, 0, 0, 0))
                entry.comment = b'preserve member comment'
                archive.writestr(entry, data)


if __name__ == '__main__':
    build()
