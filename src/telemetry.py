"""Opt-in, best-effort technical events. Never serialize engine result objects."""

import json
import os
import re
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from threading import Lock
from uuid import uuid4

_WRITE_LOCK = Lock()
_DIAGNOSES = frozenset({
    'mimetype_missing', 'mimetype_not_first', 'mimetype_incorrect_content',
    'mimetype_has_extra_field', 'rootfile_incorrect_media_type',
    'container_xml_missing', 'manifest_missing_required_property',
    'ncx_identifier_mismatch', 'manifest_missing_referenced_resource',
    'package_unique_identifier_unresolved',
})
# These values are engine-owned technical enums, never exception text.
_REASONS = frozenset('''already_valid signatures_or_encryption duplicate_zip_entries
no_supported_repair revalidation_failed unsupported_mimetype_content output_is_input output_exists
unsafe_metadata_preservation unsupported_mimetype_structure unsafe_container_creation
unsafe_manifest_property_repair unsupported_zip64 epubcheck_failure invalid_zip
output_write_failed input_read_failed unsupported_zip invalid_zip_structure
unsupported_zip_structure unsafe_container_mutation unsafe_ncx_identifier_mutation
unsafe_ncx_identifier_proof unsafe_ncx_archive ncx_evidence_missing
ncx_identifier_invalid ncx_package_ambiguous ncx_package_unsupported
ncx_unique_identifier_missing ncx_identifier_ambiguous ncx_target_ambiguous
ncx_evidence_ambiguous ncx_target_unreadable unsafe_rsc008_archive
unsafe_rsc008_manifest_mutation unsafe_opf030_archive unsafe_opf030_mutation
'''.split()) | frozenset(
    prefix + suffix
    for prefix, suffixes in (
        ('rsc008_', '''evidence_ambiguous resource_missing resource_ambiguous
resource_already_declared media_type_unsupported package_ambiguous remote_resource
properties_unsupported resource_requires_spine companion_unsupported'''),
        ('opf030_', '''evidence_ambiguous package_ambiguous unique_identifier_missing
identifier_missing identifier_ambiguous identifier_id_invalid identifier_value_invalid
reference_already_exists conflicting_repair'''),
    ) for suffix in suffixes.split()
)


def _optional(operation):
    try:
        return operation()
    except Exception:
        return None


def start():
    """No timing or I/O when disabled; configuration errors also disable collection."""
    return _optional(lambda: time.monotonic_ns()
                     if os.environ.get('EPUB_DOCTOR_TELEMETRY_ENABLED') == '1'
                     and os.environ.get('EPUB_DOCTOR_TELEMETRY_PATH') else None)


def file_size_bucket(path):
    size = Path(path).stat().st_size
    for ceiling, label in ((1, '<1MB'), (5, '1-5MB'), (20, '5-20MB'), (50, '20-50MB')):
        if size < ceiling * 1024 * 1024:
            return label
    return '>50MB'


def versions():
    # Installed distribution metadata is authoritative; no subprocess/version guesses.
    return _optional(lambda: version('epub-doctor')), None


def write_event(event):
    """Storage boundary: replace this writer to use another backend.

    Lock covers open/write/close across all threads in this process. No cross-process
    guarantee or durability guarantee is made. Newly created files are owner-only.
    """
    line = json.dumps(event, ensure_ascii=True, allow_nan=False) + '\n'
    path = Path(os.environ['EPUB_DOCTOR_TELEMETRY_PATH'])
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as output:
            output.write(line)


def _boolean(value):
    return value if type(value) is bool else None


def _code_ids(values):
    if values is None:
        return None
    return sorted({value for value in values if isinstance(value, str)
                   and re.fullmatch(r'[A-Z]{3}-[0-9]{3}', value)})


def validation_codes(validation):
    """Extract only sorted technical IDs; None means no usable validation evidence."""
    if validation is None:
        return None
    return _code_ids(message.get('ID') for message in validation.get('messages', [])
                     if isinstance(message, dict))


def record(action, path, started, result=None, validation=None, diagnoses=None,
           completed=False, evidence=None):
    """Fail-open applies only to observation, never to the underlying operation."""
    try:
        if started is None:
            return
        result = result or {}
        initial_codes = validation_codes(validation) if action == "inspect" else (evidence or {}).get("initial")
        final_codes = (evidence or {}).get("final") if action == "repair" else None
        validation = validation or {}
        repair = action == 'repair'
        confirmed = result.get('detected', []) if repair else (diagnoses or [])
        success = (result.get('final_valid') is True and bool(result.get('repaired'))
                   and bool(result.get('output_file')) and result.get('reason') is None)
        reason = result.get('reason')
        attempted = None
        if repair and completed:
            if success or reason == 'revalidation_failed':
                attempted = True
            elif not result.get('detected') or reason in {
                'already_valid', 'output_exists', 'output_is_input',
                'unsupported_mimetype_content',
                'unsupported_mimetype_structure',
            }:
                attempted = False
        doctor_version, checker_version = _optional(versions) or (None, None)
        event = {
            'schema_version': 2,
            'event_id': str(uuid4()),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'action': action,
            'operation_completed': completed,
            'initial_epubcheck_codes': _code_ids(initial_codes),
            'final_epubcheck_codes': _code_ids(final_codes),
            'confirmed_safe_repairs': sorted(set(confirmed) & _DIAGNOSES),
            'original_valid': _boolean(result.get('original_valid') if repair else validation.get('valid')),
            'repair_attempted': attempted,
            'repair_success': success if repair and completed else None,
            'final_valid': _boolean(result.get('final_valid') if repair else validation.get('valid')),
            'refusal_reason': reason if reason in _REASONS else None,
            'file_size_bucket': _optional(lambda: file_size_bucket(path)),
            'processing_time_ms': max(0, (time.monotonic_ns() - started) // 1_000_000),
            'epub_doctor_version': doctor_version,
            'epubcheck_version': checker_version,
        }
        for key in ('fatal_count', 'error_count', 'warning_count'):
            value = validation.get(key) if not repair else None
            event[key] = value if type(value) is int and value >= 0 else None
        write_event(event)
    except Exception:
        # No logging of exceptions: their text can contain private paths/content.
        pass
