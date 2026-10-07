"""Evidence-driven RSC-008 declarations, with byte-local OPF insertion.

Only bundled EPUBCheck's explicit English messages are accepted. This is not
a ZIP scanner, MIME detector, content fixer, or remote-resource downloader.
"""

import posixpath
import re
from xml.etree import ElementTree
from xml.parsers import expat
from xml.sax.saxutils import quoteattr

from .manifest_mutation import _OPF, _parse, _resolve_href
from .zip_inspection import UnsafeZipError

_REPORT = re.compile(r'Referenced resource "([^"]+)" is not declared in the OPF manifest\.')
_COMPANION = re.compile(r'Item "([^"]+)" exists in the EPUB, but is not declared in the OPF manifest\.')
_PATH = re.compile(r'[A-Za-z0-9_./-]+')
_TYPES = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.svg': 'image/svg+xml', '.css': 'text/css',
    '.xhtml': 'application/xhtml+xml', '.html': 'application/xhtml+xml',
    '.ttf': 'font/ttf', '.otf': 'font/otf', '.woff': 'font/woff', '.woff2': 'font/woff2',
}
_XHTML = '{http://www.w3.org/1999/xhtml}'
_SVG = '{http://www.w3.org/2000/svg}'


def _refuse(reason, detail):
    raise UnsafeZipError('rsc008_' + reason, detail)


def _path(value):
    if isinstance(value, str) and re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', value):
        _refuse('remote_resource', 'Schemes and remote resource targets are unsupported')
    if (not isinstance(value, str) or not _PATH.fullmatch(value)
            or any(part in ('', '.', '..') for part in value.split('/'))):
        _refuse('evidence_ambiguous', 'Require a literal canonical ASCII archive path')
    return value


def _xml(data):
    data.decode('utf-8-sig')
    parser = expat.ParserCreate()
    def forbid(*args):
        raise ValueError('DTD and non-UTF-8 XML are unsupported')
    parser.StartDoctypeDeclHandler = forbid
    parser.XmlDeclHandler = lambda version, encoding, standalone: (
        forbid() if encoding and encoding.lower() not in ('utf-8', 'utf8') else None
    )
    parser.Parse(data, True)
    root = ElementTree.fromstring(data)
    if any('{http://www.w3.org/XML/1998/namespace}base' in node.attrib for node in root.iter()):
        raise ValueError('xml:base is unsupported')
    return root


def _css_urls(data):
    """Read literal CSS url() tokens, ignoring comments and string contents.

    Escapes, imports and malformed tokens refuse rather than decode CSS URIs.
    This bounded subset covers literal font-face/image references, not all CSS.
    """
    text = data.decode('utf-8-sig')
    if '\\' in text or '\x00' in text or re.search(r'@import\b', text, re.I):
        raise ValueError('CSS escapes, imports or NUL are unsupported')
    urls, cursor = [], 0
    token = re.compile(r'url\(\s*(?:"([^"\r\n]*)"|\'([^\'\r\n]*)\'|([^\s()"\']+))\s*\)', re.I)
    while cursor < len(text):
        if text.startswith('/*', cursor):
            end = text.find('*/', cursor + 2)
            if end < 0:
                raise ValueError('Unterminated CSS comment')
            cursor = end + 2
        elif text[cursor] in ('"', "'"):
            end = text.find(text[cursor], cursor + 1)
            if end < 0:
                raise ValueError('Unterminated CSS string')
            cursor = end + 1
        elif (text[cursor:cursor + 4].lower() == 'url('
              and (cursor == 0 or not re.match(r'[\w-]', text[cursor - 1]))):
            match = token.match(text, cursor)
            if not match:
                raise ValueError('Unsupported CSS URL syntax')
            urls.append(next(value for value in match.groups() if value is not None))
            cursor = match.end()
        else:
            cursor += 1
    return urls


def _references(data, media_type):
    if media_type == 'text/css':
        return [(href, 'css') for href in _css_urls(data)]
    root = _xml(data)
    expected = {'application/xhtml+xml': _XHTML + 'html', 'image/svg+xml': _SVG + 'svg'}
    if root.tag != expected.get(media_type):
        raise ValueError('Unsupported referrer document/media type')
    if any(node.tag == _XHTML + 'base' for node in root.iter()):
        raise ValueError('HTML base is unsupported')
    contexts = {
        _XHTML + 'img': ('src',), _XHTML + 'link': ('href',),
        _XHTML + 'a': ('href',), _XHTML + 'object': ('data',),
        _XHTML + 'iframe': ('src',), _XHTML + 'audio': ('src',),
        _XHTML + 'video': ('src', 'poster'), _XHTML + 'source': ('src',),
        _SVG + 'image': ('href', '{http://www.w3.org/1999/xlink}href'),
    }
    return [(node.get(key), node.tag) for node in root.iter() for key in contexts.get(node.tag, ())
            if node.get(key) is not None]


def _media_type(path, data):
    extension = posixpath.splitext(path)[1].lower()
    media_type = _TYPES.get(extension)
    valid = False
    if extension == '.png':
        valid = (len(data) >= 33 and data[:16] == b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                 and int.from_bytes(data[16:20], 'big') > 0 and int.from_bytes(data[20:24], 'big') > 0)
    elif extension in ('.jpg', '.jpeg'):
        valid = len(data) >= 4 and data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9')
    elif extension == '.gif':
        valid = (len(data) >= 14 and data[:6] in (b'GIF87a', b'GIF89a') and data[-1:] == b';'
                 and int.from_bytes(data[6:8], 'little') > 0 and int.from_bytes(data[8:10], 'little') > 0)
    elif extension in ('.svg', '.xhtml', '.html'):
        root = _xml(data)
        valid = root.tag == (_SVG + 'svg' if extension == '.svg' else _XHTML + 'html')
        if (any(node.tag in (_XHTML + 'script', _SVG + 'script', _SVG + 'svg' if extension != '.svg' else '')
                or any(key.rsplit('}', 1)[-1].lower().startswith('on') for key in node.attrib)
                or node.get('{http://www.idpf.org/2007/ops}type') == 'toc' for node in root.iter())):
            _refuse('properties_unsupported', 'New declarations requiring nav/scripted/svg properties are unsupported')
    elif extension == '.css':
        text = data.decode('utf-8-sig')
        valid = bool(text.strip()) and '\x00' not in text and not text.lstrip().startswith('<')
    elif extension in ('.ttf', '.otf'):
        valid = len(data) >= 12 and data[:4] == (b'\x00\x01\x00\x00' if extension == '.ttf' else b'OTTO')
        tables = int.from_bytes(data[4:6], 'big')
        valid = valid and 0 < tables <= 4096 and len(data) >= 12 + 16 * tables
        if valid:
            valid = all(int.from_bytes(data[n + 8:n + 12], 'big') + int.from_bytes(data[n + 12:n + 16], 'big') <= len(data)
                        for n in range(12, 12 + 16 * tables, 16))
    elif extension in ('.woff', '.woff2'):
        valid = (len(data) >= (44 if extension == '.woff' else 48)
                 and data[:4] == (b'wOFF' if extension == '.woff' else b'wOF2')
                 and data[4:8] in (b'\x00\x01\x00\x00', b'OTTO')
                 and int.from_bytes(data[8:12], 'big') == len(data)
                 and int.from_bytes(data[12:14], 'big') > 0 and data[14:16] == b'\x00\x00')
    if not valid:
        _refuse('media_type_unsupported', 'Unsupported extension, content signature or resource structure: ' + path)
    return media_type


def add_referenced_resources(data, package_path, messages, resource_names, read_resource):
    """Prove every reported target, then insert all items atomically.

    read_resource uses the engine's bounded, unencrypted single-member reader.
    Existing OPF XML events must match exactly after removing the added events.
    """
    try:
        _path(package_path)
        if any(message['ID'] == 'OPF-014' for message in messages):
            _refuse('properties_unsupported', 'Concurrent OPF-014 mutation of the same package is unsupported')
        events, items = _parse(data)
        declared = {}
        for _, attrs, _, _ in items:
            path = _resolve_href(package_path, attrs.get('href', ''))
            if path is None:
                _refuse('remote_resource', 'Remote manifest items are outside this subset')
            _path(path)
            if path in declared:
                _refuse('resource_ambiguous', 'Aliased manifest hrefs')
            declared[path] = attrs['media-type'] if 'media-type' in attrs else ''
        reports = [message for message in messages if message['ID'] == 'RSC-008']
        if not reports or len(reports) > 128:
            _refuse('evidence_ambiguous', 'Require between 1 and 128 complete RSC-008 reports')
        targets, cache = {}, {}
        total_bytes = 0
        def read(path):
            nonlocal total_bytes
            if path not in cache:
                if resource_names.count(path) != 1:
                    _refuse('resource_missing' if path not in resource_names else 'resource_ambiguous', path)
                value = read_resource(path)
                if value is None or not value:
                    _refuse('resource_ambiguous', 'Unreadable, empty, directory or oversized resource: ' + path)
                total_bytes += len(value)
                if total_bytes > 32 * 1024 * 1024:
                    _refuse('resource_ambiguous', 'Resource proof exceeds 32 MiB')
                cache[path] = value
            return cache[path]
        for message in reports:
            text = message.get('message')
            match = _REPORT.fullmatch(text) if isinstance(text, str) else None
            locations = message.get('locations')
            if not match or not isinstance(locations, list) or not locations or message.get('additionalLocations', 0) != 0:
                _refuse('evidence_ambiguous', 'Unsupported message or incomplete RSC-008 locations')
            target = _path(match[1])
            if target in declared:
                _refuse('resource_already_declared', target)
            media_type = _media_type(target, read(target))
            for location in locations:
                referrer = _path(location.get('path') if isinstance(location, dict) else None)
                if referrer not in declared:
                    _refuse('evidence_ambiguous', 'Referrer must belong uniquely to the existing publication manifest')
                references = _references(read(referrer), declared[referrer])
                resolved = []
                aliases = {}
                for href, context in references:
                    # Fragments are not evidence for this literal-path subset.
                    if '#' in href:
                        continue
                    path = _resolve_href(referrer, href)
                    if path is None:
                        _refuse('remote_resource', 'Remote references are unsupported')
                    if path == target and media_type == 'application/xhtml+xml' and context == _XHTML + 'a':
                        _refuse('resource_requires_spine', 'Linked XHTML requires a spine item; this repair never changes spine')
                    if path in aliases and aliases[path] != href:
                        _refuse('resource_ambiguous', 'Distinct reference hrefs alias one archive member')
                    aliases[path] = href
                    resolved.append(path)
                if target not in resolved:
                    _refuse('evidence_ambiguous', 'Reported referrer does not reference target: ' + target)
            targets[target] = media_type
        # OPF-003 is the only explicitly recognized declaration companion.
        # Other errors stay in the report and must pass the real final checker.
        for message in messages:
            if message['ID'] != 'OPF-003':
                continue
            text = message.get('message')
            match = _COMPANION.fullmatch(text) if isinstance(text, str) else None
            locations = message.get('locations')
            if (not match or match[1] not in targets or not isinstance(locations, list) or not locations
                    or message.get('additionalLocations', 0) != 0
                    or any(not isinstance(location, dict) or location.get('path') not in (match[1], package_path)
                           for location in locations)):
                _refuse('companion_unsupported', 'OPF-003 must identify a fully proven RSC-008 target in this publication')
        ids = [value for event in events if event[0] == 'start' for key, value in event[2].items()
               if key in ('id', 'http://www.w3.org/XML/1998/namespace}id')]
        if len(ids) != len(set(ids)):
            _refuse('package_ambiguous', 'Duplicate existing XML IDs')
        used, additions, expected_additions = set(ids), [], []
        # Locate the parser-proven literal manifest closing tag, preserving its prefix.
        parser = expat.ParserCreate(namespace_separator='}')
        closing = []
        def end(name):
            if name == _OPF + 'manifest':
                closing.append(parser.CurrentByteIndex)
        parser.EndElementHandler = end
        parser.Parse(data, True)  # Already DTD/encoding/structure checked by _parse.
        offset = closing[0]
        match = re.match(rb'</((?:[A-Za-z_][A-Za-z0-9_.-]*:)?manifest)\s*>', data[offset:])
        if not match:
            raise ValueError('No supported literal manifest closing tag')
        qname = match[1].decode('ascii').removesuffix('manifest') + 'item'
        number = 1
        for path, media_type in sorted(targets.items()):
            while f'epub-doctor-resource-{number}' in used:
                number += 1
            identifier = f'epub-doctor-resource-{number}'
            used.add(identifier)
            href = posixpath.relpath(path, posixpath.dirname(package_path) or '.')
            if _resolve_href(package_path, href) != path:
                raise ValueError('Generated relative href does not resolve exactly')
            attrs = dict(id=identifier, href=href, **{'media-type': media_type})
            additions.append(('<' + qname + ''.join(' ' + key + '=' + quoteattr(value) for key, value in attrs.items()) + '/>').encode('utf-8'))
            expected_additions.extend([('start', _OPF + 'item', attrs), ('end', _OPF + 'item')])
        insertion = b''.join(additions)
        changed = data[:offset] + insertion + data[offset:]
        updated, updated_items = _parse(changed)
        index = next(index for index, event in enumerate(events) if event == ('end', _OPF + 'manifest'))
        if updated != events[:index] + expected_additions + events[index:] or len(updated_items) != len(items) + len(targets):
            raise ValueError('Unexpected OPF semantic change')
        return changed
    except (ValueError, LookupError, expat.ExpatError, ElementTree.ParseError) as exc:
        if isinstance(exc, UnsafeZipError):
            raise
        raise UnsafeZipError('unsafe_rsc008_manifest_mutation', str(exc)) from exc
