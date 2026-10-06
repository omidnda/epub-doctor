import os
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from .diagnoser import diagnose_epub
from .validator import EPUBCheckError, validate_epub


MIMETYPE = b"application/epub+zip"


def repair_epub(input_path: str, output_path: str) -> dict:
    """Repair only missing/misordered mimetype entries; EPUBCheck decides validity.

    Existing output files are never replaced. `repaired` is populated only
    after a valid recheck and successful publication of the new output.
    """
    source, destination = Path(input_path), Path(output_path)
    result = {
        "original_valid": None,
        "detected": [],
        "repaired": [],
        "final_valid": None,
        "output_file": None,
        "remaining_messages": [],
        "reason": None,
    }
    temporary_path = None
    stage = "input"
    try:
        original = validate_epub(str(source))
        result.update(original_valid=original["valid"], final_valid=original["valid"],
                      remaining_messages=original["messages"])
        # Inspect directly, never extract archive-controlled paths to disk.
        with ZipFile(source) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            result["detected"] = diagnose_epub(str(source), original)
            if original["valid"]:
                result["reason"] = "already_valid"
                return result
            if not result["detected"]:
                result["reason"] = "no_supported_repair"
                return result
            publication = original["publication"]
            if (publication.get("hasSignatures") or publication.get("hasEncryption")
                    or "META-INF/signatures.xml" in names
                    or "META-INF/encryption.xml" in names
                    or any(entry.flag_bits & 1 for entry in entries)):
                result["reason"] = "signatures_or_encryption"
                return result
            if len(names) != len(set(names)):
                result["reason"] = "duplicate_zip_entries"
                return result
            if "mimetype" in names and archive.read("mimetype") != MIMETYPE:
                result["reason"] = "unsupported_mimetype_content"
                return result
            if source.resolve() == destination.resolve():
                result["reason"] = "output_is_input"
                return result
            if destination.exists() or destination.is_symlink():
                result["reason"] = "output_exists"
                return result

            stage = "output"
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, suffix=".epub", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
            with ZipFile(temporary_path, "w") as rebuilt:
                rebuilt.writestr("mimetype", MIMETYPE, compress_type=ZIP_STORED)
                for entry in entries:
                    if entry.filename != "mimetype":
                        # Preserve entry metadata and bytes, with new ZIP compression.
                        rebuilt.writestr(entry, archive.read(entry),
                                         compress_type=ZIP_DEFLATED)

        stage = "revalidation"
        final = validate_epub(str(temporary_path))
        result.update(final_valid=final["valid"], remaining_messages=final["messages"])
        stage = "output"
        # Atomic, exclusive publication: a concurrent writer cannot be overwritten.
        os.link(temporary_path, destination)
        result["output_file"] = str(destination)
        if final["valid"]:
            result["repaired"] = result["detected"].copy()
        else:
            result["reason"] = "revalidation_failed"
    except EPUBCheckError as exc:
        result.update(reason="epubcheck_failure", detail=str(exc), final_valid=None)
    except BadZipFile as exc:
        result.update(reason="invalid_zip", detail=str(exc))
    except OSError as exc:
        result.update(reason="output_write_failed" if stage == "output" else "input_read_failed",
                      detail=str(exc))
    except (RuntimeError, NotImplementedError) as exc:
        result.update(reason="unsupported_zip", detail=str(exc))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return result
