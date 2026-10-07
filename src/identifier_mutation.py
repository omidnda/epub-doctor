"""OPF-030: retarget an existing reference to the sole existing identifier.

No identifier generation, normalization, renaming, metadata or NCX mutation.
"""

import re
from xml.parsers import expat

from .manifest_mutation import _tag_spans
from .zip_inspection import UnsafeZipError

_OPF = 'http://www.idpf.org/2007/opf}'
_DC = 'http://purl.org/dc/elements/1.1/}'
_XML = 'http://www.w3.org/XML/1998/namespace}'
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9._-]*')


def _refuse(reason, detail):
    raise UnsafeZipError('opf030_' + reason, detail)


def _parse(data):
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('OPF exceeds 8 MiB')
    data.decode('utf-8-sig')
    parser = expat.ParserCreate(namespace_separator='}')
    stack, events, identifiers, ids, roots = [], [], [], [], []
    metadata = 0

    def forbid(*args):
        raise ValueError('DTD/entity declarations are unsupported')

    def declaration(version, encoding, standalone):
        if encoding and encoding.lower() not in ('utf-8', 'utf8'):
            raise ValueError('Only UTF-8 OPF mutation is supported')

    def start(name, attrs):
        nonlocal metadata
        if len(events) >= 100000 or len(stack) >= 128:
            raise ValueError('OPF event/depth limit exceeded')
        stack.append(name)
        if _XML + 'base' in attrs:
            raise ValueError('xml:base is unsupported')
        if name == _OPF + 'package' and len(stack) != 1:
            _refuse('package_ambiguous', 'Nested package elements are unsupported')
        if len(stack) == 1:
            if name != _OPF + 'package' or attrs.get('version') not in ('2.0', '3.0'):
                _refuse('package_ambiguous', 'No supported EPUB2/EPUB3 package root')
            spans, _ = _tag_spans(data, parser.CurrentByteIndex)
            roots.append((len(events), attrs, spans.get(b'unique-identifier')))
        if name == _OPF + 'metadata':
            if stack != [_OPF + 'package', _OPF + 'metadata']:
                _refuse('identifier_ambiguous', 'Metadata must be a direct package child')
            metadata += 1
        if name == _DC + 'identifier':
            if stack != [_OPF + 'package', _OPF + 'metadata', _DC + 'identifier']:
                _refuse('identifier_ambiguous', 'Identifier must be a direct metadata child')
            identifiers.append((attrs, []))
        elif _DC + 'identifier' in stack:
            _refuse('identifier_value_invalid', 'Identifier cannot have child elements')
        ids.extend(value for key, value in attrs.items() if key in ('id', _XML + 'id'))
        events.append(('start', name, attrs))

    def text(value):
        if stack and stack[-1] == _DC + 'identifier':
            identifiers[-1][1].append(value)
        if events and events[-1][0] == 'text':
            events[-1] = ('text', events[-1][1] + value)
        else:
            events.append(('text', value))

    def end(name):
        events.append(('end', name))
        stack.pop()

    def comment(value):
        if _DC + 'identifier' in stack:
            _refuse('identifier_value_invalid', 'Identifier comments/processing instructions are unsupported')
        events.append(('comment', value))

    def instruction(target, value):
        if _DC + 'identifier' in stack:
            _refuse('identifier_value_invalid', 'Identifier comments/processing instructions are unsupported')
        events.append(('pi', target, value))

    parser.XmlDeclHandler = declaration
    parser.StartDoctypeDeclHandler = forbid
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.CommentHandler = comment
    parser.ProcessingInstructionHandler = instruction
    parser.StartNamespaceDeclHandler = lambda prefix, uri: events.append(('namespace', prefix, uri))
    parser.EndNamespaceDeclHandler = lambda prefix: events.append(('end_namespace', prefix))
    parser.Parse(data, True)
    if metadata != 1:
        _refuse('identifier_ambiguous', 'Require exactly one direct OPF metadata element')
    return events, roots[0], identifiers, ids


def correct_unique_identifier(data: bytes, package_path: str, messages: list[dict]) -> bytes:
    """Prove the sole candidate and exact evidence, then verify a value-only edit."""
    try:
        events, (index, attrs, span), identifiers, ids = _parse(data)
        reference = attrs.get('unique-identifier')
        if not reference or span is None:
            _refuse('unique_identifier_missing', 'Existing nonempty literal unique-identifier is required')
        if not _NAME.fullmatch(reference):
            _refuse('evidence_ambiguous', 'Current reference is outside the supported ASCII NCName subset')
        reports = [message for message in messages if message['ID'] == 'OPF-030']
        expected_message = f'The unique-identifier "{reference}" was not found.'
        if not reports or any(
                message.get('message') != expected_message
                or not isinstance(message.get('locations'), list) or not message['locations']
                or message.get('additionalLocations', 0) != 0
                or any(not isinstance(location, dict) or location.get('path') != package_path
                       for location in message['locations']) for message in reports):
            _refuse('evidence_ambiguous', 'OPF-030 must identify the current reference and proven package')
        if reference in ids:
            _refuse('reference_already_exists', 'Current reference already names a package element; not an absent-ID repair')
        if not identifiers:
            _refuse('identifier_missing', 'No existing dc:identifier')
        if len(identifiers) != 1:
            _refuse('identifier_ambiguous', 'Multiple identifiers cannot establish a non-heuristic target')
        candidate, content = identifiers[0]
        target = candidate.get('id', '')
        if not _NAME.fullmatch(target):
            _refuse('identifier_id_invalid', 'Candidate needs an existing nonempty ASCII NCName id')
        if ids.count(target) != 1 or len(ids) != len(set(ids)):
            _refuse('identifier_ambiguous', 'Candidate ID collision or duplicate existing package IDs')
        value = ''.join(content)
        if not value or value != value.strip() or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            _refuse('identifier_value_invalid', 'Identifier requires nonempty plain text without edge whitespace/controls')
        if any(message['ID'] in ('OPF-048', 'OPF-085') for message in messages):
            _refuse('identifier_value_invalid', 'Validator reports missing reference or invalid UUID metadata')
        # This exact EPUB3 RSC-005 is the only recognized direct companion.
        companion = ('Error while parsing file: package element unique-identifier attribute '
                     f'does not resolve to a dc:identifier element (given reference was "{reference}")')
        for message in messages:
            text = message.get('message', '')
            if message['ID'] == 'RSC-005' and isinstance(text, str) and text.startswith(
                    'Error while parsing file: package element unique-identifier attribute does not resolve'):
                locations = message.get('locations')
                if (text != companion or not isinstance(locations, list) or not locations
                        or message.get('additionalLocations', 0) != 0
                        or any(not isinstance(location, dict) or location.get('path') != package_path for location in locations)):
                    _refuse('evidence_ambiguous', 'Inconsistent identifier-reference companion evidence')
        start, end = span
        changed = data[:start] + target.encode('ascii') + data[end:]
        updated, (_, updated_attrs, _), _, _ = _parse(changed)
        expected = events.copy()
        expected[index] = ('start', _OPF + 'package', {**attrs, 'unique-identifier': target})
        if updated != expected or updated_attrs != {**attrs, 'unique-identifier': target}:
            raise ValueError('Unexpected OPF semantic change')
        return changed
    except (ValueError, IndexError, expat.ExpatError) as exc:
        if isinstance(exc, UnsafeZipError):
            raise
        raise UnsafeZipError('unsafe_opf030_mutation', str(exc)) from exc
