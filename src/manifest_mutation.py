"""OPF-014 manifest-only planning and byte-local UTF-8 XML mutation.

EPUBCheck messages are the sole detector; no resource contents are analyzed.
The complete message set must map safely before any edited bytes are returned.
"""

import re
from urllib.parse import urlsplit
from xml.parsers import expat

from .zip_inspection import UnsafeZipError


_OPF = 'http://www.idpf.org/2007/opf}'
_XML_BASE = 'http://www.w3.org/XML/1998/namespace}base'
_SPACE = b' \t\r\n'
_MESSAGES = {
    f'The property "{token}" should be declared in the OPF file.': token
    for token in ('scripted', 'svg', 'remote-resources')
}


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r'[ \t\r\n]+', value) if token]


def _resolve_href(package_path: str, href: str) -> str | None:
    """Resolve literal relative URI paths; decline percent/base/query ambiguity.

    Remote manifest entries cannot match a local report location. Dot segments
    may ascend within the archive but never above its root. Unsupported hrefs
    refuse the whole mapping, rather than concealing a possible second match.
    """
    if (not href or any(char in href for char in '\\%?#')
            or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in href)):
        raise ValueError('Unsafe or unsupported manifest href')
    uri = urlsplit(href)
    if uri.scheme or uri.netloc:
        if uri.scheme in ('http', 'https') and uri.netloc:
            return None
        raise ValueError('Unsupported manifest URI')
    if href.startswith('/') or ':' in href:
        raise ValueError('Absolute or ambiguous manifest href')
    parts = package_path.split('/')[:-1]
    for part in href.split('/'):
        if not part:
            raise ValueError('Empty manifest path segment')
        if part == '.':
            continue
        if part == '..':
            if not parts:
                raise ValueError('Manifest href traverses above archive root')
            parts.pop()
        else:
            parts.append(part)
    if href.endswith(('/', '/.', '/..')) or not parts:
        raise ValueError('Manifest href does not name a resource')
    return '/'.join(parts)


def _tag_spans(data: bytes, offset: int):
    """Locate only attributes in an Expat-proven start tag; respect quotes."""
    if data[offset:offset + 1] != b'<':
        raise ValueError('No literal item start tag')
    cursor = offset + 1
    while data[cursor] not in _SPACE + b'/>':
        cursor += 1
    spans = {}
    while True:
        while data[cursor] in _SPACE:
            cursor += 1
        if data[cursor] in b'/>':
            return spans, cursor
        name_start = cursor
        while data[cursor] not in _SPACE + b'=':
            cursor += 1
        name = data[name_start:cursor]
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
        end = data.index(bytes([quote]), start)
        if name in spans:
            raise ValueError('Duplicate literal attribute')
        spans[name] = (start, end)
        cursor = end + 1


def _parse(data: bytes):
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('Oversized OPF')
    data.decode('utf-8-sig')
    parser = expat.ParserCreate(namespace_separator='}')
    stack, events, items = [], [], []
    manifests = 0

    def declaration(version, encoding, standalone):
        if encoding and encoding.lower() not in ('utf-8', 'utf8'):
            raise ValueError('Only UTF-8 OPF mutation is supported')

    def forbid_dtd(*args):
        raise ValueError('DTD is not supported for OPF mutation')

    def start(name, attributes):
        nonlocal manifests
        stack.append(name)
        if len(events) >= 100000:
            raise ValueError('OPF event limit exceeded')
        if _XML_BASE in attributes:
            raise ValueError('xml:base is outside the safe mapping subset')
        if len(stack) == 1 and (name != _OPF + 'package' or attributes.get('version') != '3.0'):
            raise ValueError('No EPUB3 OPF package root')
        if name == _OPF + 'manifest':
            if stack != [_OPF + 'package', _OPF + 'manifest']:
                raise ValueError('Unexpected manifest location')
            manifests += 1
        if name == _OPF + 'item':
            if stack != [_OPF + 'package', _OPF + 'manifest', _OPF + 'item']:
                raise ValueError('Unexpected manifest item location')
            spans, closing = _tag_spans(data, parser.CurrentByteIndex)
            items.append((len(events), attributes, spans, closing))
        events.append(('start', name, attributes))

    def text(value):
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
    if manifests != 1 or not items:
        raise ValueError('No unique nonempty manifest')
    ids = [attributes.get('id') for _, attributes, _, _ in items]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError('Missing or duplicate manifest IDs')
    return events, items


def add_required_manifest_properties(data: bytes, package_path: str,
                                     messages: list[dict], resource_names: set[str]) -> bytes:
    """Prove all OPF-014 targets, edit exact start tags, and verify XML semantics.

    Any unsupported/inconsistent message or mapping refuses the entire OPF-014
    set. Duplicate evidence is coalesced; tokens already in the input are refused.
    """
    try:
        reported = [message for message in messages if message['ID'] == 'OPF-014']
        if not reported:
            raise ValueError('No OPF-014 evidence')
        events, items = _parse(data)
        mapping = {}
        for index, (_, attributes, _, _) in enumerate(items):
            path = _resolve_href(package_path, attributes.get('href', ''))
            if path is not None:
                mapping.setdefault(path, []).append(index)
        additions = {}
        for message in reported:
            text = message.get('message')
            token = _MESSAGES.get(text) if isinstance(text, str) else None
            locations = message.get('locations')
            if token is None or not isinstance(locations, list) or not locations:
                raise ValueError('Unsupported OPF-014 message or missing locations')
            if message.get('additionalLocations', 0) != 0:
                raise ValueError('OPF-014 report omits additional locations')
            for location in locations:
                path = location.get('path') if isinstance(location, dict) else None
                if (not isinstance(path, str) or not path
                        or any(part in ('', '.', '..') for part in path.split('/'))
                        or any(char in path for char in '\\:%?#')
                        or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in path)):
                    raise ValueError('Unsafe OPF-014 resource location')
                matches = mapping.get(path, [])
                if len(matches) != 1 or path not in resource_names:
                    raise ValueError('Resource does not map to one existing manifest item')
                index = matches[0]
                attributes = items[index][1]
                if token in _tokens(attributes.get('properties', '')):
                    raise ValueError('Reported property is already declared')
                additions.setdefault(index, set()).add(token)
        edits = []
        expected = list(events)
        for index, tokens in sorted(additions.items()):
            event_index, attributes, spans, closing = items[index]
            suffix = ' '.join(sorted(tokens))
            if 'properties' in attributes:
                if b'properties' not in spans:
                    raise ValueError('No literal properties attribute')
                _, offset = spans[b'properties']
                suffix = (' ' if attributes['properties'] else '') + suffix
                edits.append((offset, suffix.encode('ascii')))
                properties = attributes['properties'] + suffix
            else:
                edits.append((closing, (' properties="' + suffix + '"').encode('ascii')))
                properties = suffix
            expected[event_index] = ('start', _OPF + 'item', {**attributes, 'properties': properties})
        changed = data
        for offset, inserted in sorted(edits, reverse=True):
            changed = changed[:offset] + inserted + changed[offset:]
        updated, updated_items = _parse(changed)
        if updated != expected or len(updated_items) != len(items):
            raise ValueError('Unexpected OPF semantic change')
        for index, tokens in additions.items():
            actual = _tokens(updated_items[index][1]['properties'])
            if any(actual.count(token) != 1 for token in tokens):
                raise ValueError('Required property token verification failed')
        return changed
    except (ValueError, IndexError, expat.ExpatError) as exc:
        raise UnsafeZipError('unsafe_manifest_property_repair', str(exc)) from exc
