"""NCX-001 only: replace one proven dtb:uid attribute without reserialization."""

from xml.parsers import expat

from .manifest_mutation import _tag_spans
from .zip_inspection import UnsafeZipError

_NCX = 'http://www.daisy.org/z3986/2005/ncx/}'
_XML_BASE = 'http://www.w3.org/XML/1998/namespace}base'


def _parse(data: bytes):
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('Oversized NCX')
    data.decode('utf-8-sig')
    parser = expat.ParserCreate(namespace_separator='}')
    stack, events, targets = [], [], []
    heads = 0

    def declaration(version, encoding, standalone):
        if encoding and encoding.lower() not in ('utf-8', 'utf8'):
            raise ValueError('Only UTF-8 NCX mutation is supported')

    def forbid_dtd(*args):
        raise ValueError('DTD/entity NCX mutation is unsupported')

    def start(name, attributes):
        nonlocal heads
        if stack == [_NCX + 'ncx', _NCX + 'head', _NCX + 'meta']:
            raise ValueError('NCX meta cannot have child elements')
        if len(events) >= 100000 or len(stack) >= 128:
            raise ValueError('NCX parsing limit exceeded')
        stack.append(name)
        if _XML_BASE in attributes:
            raise ValueError('xml:base is unsupported')
        if len(stack) == 1 and (name != _NCX + 'ncx' or attributes.get('version') != '2005-1'):
            raise ValueError('No supported NCX root')
        if name == _NCX + 'head':
            if stack != [_NCX + 'ncx', _NCX + 'head']:
                raise ValueError('Unexpected NCX head location')
            heads += 1
        if attributes.get('name') == 'dtb:uid':
            if name != _NCX + 'meta' or stack != [_NCX + 'ncx', _NCX + 'head', _NCX + 'meta']:
                raise ValueError('Unexpected dtb:uid target location')
            spans, _ = _tag_spans(data, parser.CurrentByteIndex)
            if 'content' not in attributes or b'content' not in spans:
                raise ValueError('No literal dtb:uid content attribute')
            targets.append((len(events), attributes, spans[b'content']))
        events.append(('start', name, attributes))

    def text(value):
        if stack == [_NCX + 'ncx', _NCX + 'head', _NCX + 'meta'] and value.strip():
            raise ValueError('NCX meta cannot have text content')
        if events and events[-1][0] == 'text':
            events[-1] = ('text', events[-1][1] + value)
        else:
            events.append(('text', value))

    def end(name):
        events.append(('end', name))
        stack.pop()

    parser.XmlDeclHandler = declaration
    parser.StartDoctypeDeclHandler = forbid_dtd
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.CommentHandler = lambda value: events.append(('comment', value))
    parser.ProcessingInstructionHandler = lambda target, value: events.append(('pi', target, value))
    parser.StartNamespaceDeclHandler = lambda prefix, uri: events.append(('namespace', prefix, uri))
    parser.EndNamespaceDeclHandler = lambda prefix: events.append(('end_namespace', prefix))
    parser.Parse(data, True)
    if heads != 1:
        raise ValueError(f'Expected exactly one NCX head; found {heads}')
    if len(targets) != 1:
        raise ValueError(f'Expected exactly one head/meta dtb:uid target; found {len(targets)}')
    return events, targets[0]


def correct_ncx_identifier(data: bytes, canonical: str) -> bytes:
    """Caller proves package linkage and validator evidence; verify byte-local edit."""
    try:
        events, (index, attributes, (start, end)) = _parse(data)
        if not attributes['content'] or attributes['content'] == canonical:
            raise ValueError('NCX target is empty or already matches; not the mismatch subset')
        if not canonical or canonical != canonical.strip() or any(
                ord(char) < 32 or 127 <= ord(char) <= 159 for char in canonical):
            raise ValueError('Invalid or whitespace-normalized canonical identifier')
        value = (canonical.replace('&', '&amp;').replace('<', '&lt;')
                 .replace('"', '&quot;').replace("'", '&apos;')).encode('utf-8')
        changed = data[:start] + value + data[end:]
        updated, (_, new_attributes, _) = _parse(changed)
        expected = events.copy()
        expected[index] = ('start', _NCX + 'meta', {**attributes, 'content': canonical})
        if updated != expected or new_attributes != {**attributes, 'content': canonical}:
            raise ValueError('Unexpected NCX semantics changed')
        return changed
    except (ValueError, IndexError, expat.ExpatError) as exc:
        raise UnsafeZipError('unsafe_ncx_identifier_mutation', str(exc)) from exc
