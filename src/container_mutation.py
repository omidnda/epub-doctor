"""Byte-local XML mutation after a confirmed rootfile diagnosis."""

from xml.parsers import expat

from .zip_inspection import UnsafeZipError


_OCF = 'urn:oasis:names:tc:opendocument:xmlns:container}'
_MEDIA_TYPE = 'application/oebps-package+xml'
_SPACE = b' \t\r\n'


def _attribute_span(data: bytes, offset: int) -> tuple[int, int]:
    """Scan only the parser-identified start tag, respecting quoted values."""
    if data[offset:offset + 1] != b'<':
        raise ValueError('No literal start tag')
    cursor = offset + 1
    while data[cursor] not in _SPACE + b'/>':
        cursor += 1
    spans = []
    while True:
        while data[cursor] in _SPACE:
            cursor += 1
        if data[cursor] in b'/>':
            break
        start = cursor
        while data[cursor] not in _SPACE + b'=':
            cursor += 1
        name = data[start:cursor]
        while data[cursor] in _SPACE:
            cursor += 1
        if data[cursor] != ord('='):
            raise ValueError('Uncertain attribute syntax')
        cursor += 1
        while data[cursor] in _SPACE:
            cursor += 1
        quote = data[cursor]
        if quote not in (ord('"'), ord("'")):
            raise ValueError('Unquoted attribute')
        start = cursor + 1
        cursor = data.index(bytes([quote]), start)
        if name == b'media-type':
            spans.append((start, cursor))
        cursor += 1
    if len(spans) != 1:
        raise ValueError('No unique literal media-type attribute')
    return spans[0]


def _locate(data: bytes):
    # Expat byte offsets match the raw document only in this supported encoding.
    data.decode('utf-8-sig')
    parser = expat.ParserCreate(namespace_separator='}')
    stack = []
    roots = []

    def declaration(version, encoding, standalone):
        if encoding and encoding.lower() not in ('utf-8', 'utf8'):
            raise ValueError('Only UTF-8 mutation is supported')

    def forbid_dtd(*args):
        raise ValueError('DTD mutation is not supported')

    def start(name, attributes):
        stack.append(name)
        if name == _OCF + 'rootfile':
            if stack != [_OCF + 'container', _OCF + 'rootfiles', _OCF + 'rootfile']:
                raise ValueError('Unexpected rootfile location')
            roots.append((_attribute_span(data, parser.CurrentByteIndex), attributes))

    parser.XmlDeclHandler = declaration
    parser.StartDoctypeDeclHandler = forbid_dtd
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda name: stack.pop()
    parser.Parse(data, True)
    if len(roots) != 1:
        raise ValueError('No unique rootfile')
    return roots[0]


def correct_rootfile_media_type(data: bytes) -> bytes:
    """Replace one proven attribute value without serializing the XML tree.

    The caller must first confirm the archive-level diagnosis and ZIP safety.
    Missing attributes and non-UTF-8 documents intentionally remain unsupported.
    """
    try:
        (start, end), attributes = _locate(data)
        if attributes.get('media-type') == _MEDIA_TYPE:
            raise ValueError('Media type is already correct')
        changed = data[:start] + _MEDIA_TYPE.encode('ascii') + data[end:]
        _, updated = _locate(changed)
        if updated != {**attributes, 'media-type': _MEDIA_TYPE}:
            raise ValueError('Unexpected XML attribute change')
        return changed
    except (ValueError, IndexError, expat.ExpatError) as exc:
        raise UnsafeZipError('unsafe_container_mutation', str(exc)) from exc
