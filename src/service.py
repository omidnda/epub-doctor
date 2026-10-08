"""Stable internal boundary for EPUB inspection and conservative repair.

Inspection propagates existing validator/diagnoser exceptions: FileNotFoundError
or other OSError for input access, EPUBCheckError for execution/report failures,
and ZIP/decompression exceptions or UnsafeZipError for diagnosis failures.
No partial inspection result is returned on failure. Repair retains the engine's
structured failure results (including optional detail). Unexpected programming
errors propagate from both entry points.
"""

from . import telemetry
from .diagnoser import diagnose_epub
from .repairer import repair_epub
from .validator import validate_epub


def inspect_epub(path: str) -> dict:
    """Validate and diagnose read-only; create no output EPUB.

    safe_repairs contains existing diagnosis names. repairable means only that
    this list is non-empty; repair can still be refused by the pipeline's safety
    checks or fail revalidation. Messages/publication retain EPUBCheck's fields.
    EPUBCheck itself uses a temporary JSON report, cleaned up by the validator.
    """
    started = _observe(telemetry.start)
    validation = None
    safe_repairs = None
    try:
        validation = validate_epub(path)
        safe_repairs = diagnose_epub(path, validation)
        result = _inspection_result(validation, safe_repairs)
    except Exception:
        _observe(telemetry.record, "inspect", path, started, validation=validation, diagnoses=safe_repairs)
        raise
    _observe(telemetry.record, "inspect", path, started, validation=validation,
             diagnoses=safe_repairs, completed=True)
    return result


def _inspection_result(validation, safe_repairs):
    return {
        "valid": validation["valid"],
        "repairable": len(safe_repairs) > 0,
        "safe_repairs": safe_repairs,
        "fatal_count": validation["fatal_count"],
        "error_count": validation["error_count"],
        "warning_count": validation["warning_count"],
        "messages": validation["messages"],
        "publication": validation["publication"],
    }


def repair_epub_file(input_path: str, output_path: str) -> dict:
    """Delegate to the safe pipeline, preserving its result and failure policy."""
    started = _observe(telemetry.start)
    try:
        result = repair_epub(input_path, output_path)
    except Exception:
        _observe(telemetry.record, "repair", input_path, started)
        raise
    _observe(telemetry.record, "repair", input_path, started, result=result, completed=True)
    return result


def _observe(operation, *args, **kwargs):
    """Keep even unexpected observer failures outside the service contract."""
    try:
        return operation(*args, **kwargs)
    except Exception:
        return None
