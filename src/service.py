"""Stable internal boundary for EPUB inspection and conservative repair.

Inspection propagates existing validator/diagnoser exceptions: FileNotFoundError
or other OSError for input access, EPUBCheckError for execution/report failures,
and ZIP/decompression exceptions or UnsafeZipError for diagnosis failures.
No partial inspection result is returned on failure. Repair retains the engine's
structured failure results (including optional detail). Unexpected programming
errors propagate from both entry points.
"""

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
    validation = validate_epub(path)
    safe_repairs = diagnose_epub(path, validation)
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
    return repair_epub(input_path, output_path)
