import shutil
import tempfile
from copy import copy
from lzma import LZMAError
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, LargeZipFile, ZIP_STORED, ZipFile, ZipInfo
from zlib import error as ZlibError

from . import diagnoser
from .container_mutation import correct_rootfile_media_type
from .diagnoser import diagnose_epub
from .validator import EPUBCheckError, validate_epub
from .zip_inspection import UnsafeZipError, inspect_local_headers


MIMETYPE = b"application/epub+zip"


def _build_container_xml(package_path: str) -> bytes:
    namespace = "urn:oasis:names:tc:opendocument:xmlns:container"
    media_type = "application/oebps-package+xml"
    container = ElementTree.Element("container", {"version": "1.0", "xmlns": namespace})
    rootfiles = ElementTree.SubElement(container, "rootfiles")
    ElementTree.SubElement(rootfiles, "rootfile", {"full-path": package_path, "media-type": media_type})
    data = ElementTree.tostring(container, encoding="UTF-8", xml_declaration=True)
    parsed = ElementTree.fromstring(data)
    prefix = "{" + namespace + "}"
    groups = parsed.findall(prefix + "rootfiles")
    candidates = parsed.findall("./" + prefix + "rootfiles/" + prefix + "rootfile")
    if (parsed.tag != prefix + "container" or parsed.get("version") != "1.0"
            or len(groups) != 1 or len(list(parsed.iter(prefix + "rootfiles"))) != 1
            or len(candidates) != 1
            or len(list(parsed.iter(prefix + "rootfile"))) != 1
            or candidates[0].get("full-path") != package_path
            or candidates[0].get("media-type") != media_type):
        raise UnsafeZipError("unsafe_container_creation", "Generated container semantics differ")
    return data


def repair_epub(input_path: str, output_path: str) -> dict:
    """Repair confirmed mimetype/container/manifest/NCX issues; EPUBCheck decides validity.

    Existing output files are never replaced. `repaired` is populated only
    after a valid recheck and successful publication of the new output.
    """
    return _repair_epub(input_path, output_path)


def _observe_validation(observer, phase, validation):
    """Observation must never change engine results or exception handling."""
    if observer is not None:
        try:
            observer(phase, validation)
        except Exception:
            pass


def _repair_epub(input_path: str, output_path: str, *, _validation_observer=None) -> dict:
    """Internal pipeline; public callers receive only the existing repair result."""
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
    output_created = False
    stage = "input"
    try:
        original = validate_epub(str(source))
        _observe_validation(_validation_observer, "initial", original)
        result.update(original_valid=original["valid"], final_valid=original["valid"],
                      remaining_messages=original["messages"])
        # Inspect directly, never extract archive-controlled paths to disk.
        with ZipFile(source) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if original["valid"]:
                result["reason"] = "already_valid"
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
            result["detected"] = diagnose_epub(str(source), original)
            ncx_replacement = None
            if any(message["ID"] == "NCX-001" for message in original["messages"]):
                # Reconfirm full proof and retain precise refusal reasons even
                # when diagnosis intentionally exposes no safe repair family.
                ncx_replacement = diagnoser._ncx_identifier_replacement(archive, original)
            resource_replacement = None
            if any(message["ID"] == "RSC-008" for message in original["messages"]):
                resource_replacement = diagnoser._referenced_resource_replacement(archive, original)
            identifier_replacement = None
            if any(message["ID"] == "OPF-030" for message in original["messages"]):
                identifier_replacement = diagnoser._unique_identifier_replacement(archive, original)
            if not result["detected"]:
                result["reason"] = "no_supported_repair"
                return result
            if ("mimetype" in names and archive.read("mimetype") != MIMETYPE
                    and "mimetype_incorrect_content" not in result["detected"]):
                result["reason"] = "unsupported_mimetype_content"
                return result
            if source.resolve() == destination.resolve():
                result["reason"] = "output_is_input"
                return result
            if destination.exists() or destination.is_symlink():
                result["reason"] = "output_exists"
                return result

            local_extras = inspect_local_headers(archive)
            if any(entry.filename != "mimetype"
                   and (local_extras[entry.filename] or entry.extra) for entry in entries):
                result["reason"] = "unsafe_metadata_preservation"
                result["detail"] = "Non-mimetype ZIP extra fields cannot be faithfully rebuilt"
                return result

            repair_mimetype = any(item.startswith("mimetype_") for item in result["detected"])
            replacements = {}
            if "package_unique_identifier_unresolved" in result["detected"]:
                if identifier_replacement is None:
                    raise UnsafeZipError("opf030_evidence_ambiguous", "OPF-030 proof was not reconfirmed")
                package_path, changed = identifier_replacement
                replacements[package_path] = changed
            if "manifest_missing_referenced_resource" in result["detected"]:
                if resource_replacement is None:
                    raise UnsafeZipError("rsc008_evidence_ambiguous", "RSC-008 evidence was not reconfirmed")
                package_path, changed = resource_replacement
                replacements[package_path] = changed
            if "ncx_identifier_mismatch" in result["detected"]:
                if ncx_replacement is None:
                    raise UnsafeZipError("ncx_evidence_missing", "NCX-001 evidence was not reconfirmed")
                ncx_path, changed = ncx_replacement
                replacements[ncx_path] = changed
            new_container = None
            if "manifest_missing_required_property" in result["detected"]:
                replacement = diagnoser._manifest_property_replacement(archive, original)
                if replacement is None:
                    raise UnsafeZipError("unsafe_manifest_property_repair", "OPF-014 evidence was not reconfirmed")
                package_path, changed = replacement
                replacements[package_path] = changed
            if "container_xml_missing" in result["detected"]:
                package_path = diagnoser._find_unique_package_path(archive, original)
                if package_path is None or result["detected"] != ["container_xml_missing"]:
                    raise UnsafeZipError("unsafe_container_creation", "Unique package evidence was not confirmed")
                new_container = _build_container_xml(package_path)
            if "rootfile_incorrect_media_type" in result["detected"]:
                if not repair_mimetype and (not names or names[0] != "mimetype"
                        or archive.getinfo("mimetype").compress_type != ZIP_STORED
                        or local_extras["mimetype"] or archive.getinfo("mimetype").extra):
                    result["reason"] = "unsupported_mimetype_structure"
                    return result
                replacements["META-INF/container.xml"] = correct_rootfile_media_type(
                    archive.read("META-INF/container.xml")
                )

            stage = "output"
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, suffix=".epub", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
            with ZipFile(temporary_path, "w", allowZip64=False) as rebuilt:
                rebuilt.comment = archive.comment
                if repair_mimetype:
                    rebuilt.writestr("mimetype", MIMETYPE, compress_type=ZIP_STORED)
                for entry in entries:
                    if entry.filename != "mimetype" or not repair_mimetype:
                        # Preserve metadata, bytes, and the original compression method.
                        copied = copy(entry)
                        data = (replacements[entry.filename] if entry.filename in replacements
                                else archive.read(entry))
                        rebuilt.writestr(copied, data,
                                         compress_type=entry.compress_type)
                        # zipfile supplies defaults for zero attributes while writing.
                        copied.external_attr = entry.external_attr
                        if entry.filename == "mimetype" and new_container is not None:
                            container_entry = ZipInfo("META-INF/container.xml", (2020, 1, 1, 0, 0, 0))
                            container_entry.create_system = 3
                            container_entry.external_attr = 0o600 << 16
                            rebuilt.writestr(container_entry, new_container, compress_type=ZIP_STORED)

            # Refuse any additional metadata normalization performed by zipfile.
            with ZipFile(temporary_path) as rebuilt:
                metadata = ("date_time", "compress_type", "comment", "extra", "internal_attr",
                            "external_attr", "create_system", "create_version", "extract_version")
                for entry in entries:
                    if entry.filename != "mimetype" or not repair_mimetype:
                        copied = rebuilt.getinfo(entry.filename)
                        if any(getattr(copied, field) != getattr(entry, field) for field in metadata):
                            raise UnsafeZipError("unsafe_metadata_preservation",
                                                 f"ZIP metadata changed for {entry.filename}")

        stage = "revalidation"
        final = validate_epub(str(temporary_path))
        _observe_validation(_validation_observer, "final", final)
        result.update(final_valid=final["valid"], remaining_messages=final["messages"])
        if ("package_unique_identifier_unresolved" in result["detected"]
                and any(message["ID"] == "OPF-085" for message in final["messages"])):
            # Identifier checks can be deferred until the reference resolves.
            # A UUID warning invalidates this repair's identifier proof even
            # when the general validator's warnings policy reports valid=True.
            result.update(reason="opf030_identifier_value_invalid",
                          detail="Final EPUBCheck reports an invalid UUID for the existing identifier")
            return result
        if not final["valid"]:
            result["reason"] = "revalidation_failed"
            return result
        stage = "output"
        # Exclusive creation prevents overwrite even if a writer races the precheck.
        # Copying is portable, but the destination is visible while it is written.
        with temporary_path.open("rb") as validated:
            with destination.open("xb") as output:
                output_created = True
                shutil.copyfileobj(validated, output)
        result["output_file"] = str(destination)
        result["repaired"] = result["detected"].copy()
    except UnsafeZipError as exc:
        result.update(reason=exc.reason, detail=str(exc))
    except LargeZipFile as exc:
        result.update(reason="unsupported_zip64", detail=str(exc))
    except EPUBCheckError as exc:
        result.update(reason="epubcheck_failure", detail=str(exc), final_valid=None)
    except (BadZipFile, EOFError, UnicodeError, LZMAError, ZlibError) as exc:
        result.update(reason="invalid_zip", detail=str(exc))
    except OSError as exc:
        result.update(reason="output_write_failed" if stage == "output" else "input_read_failed",
                      detail=str(exc))
    except (RuntimeError, NotImplementedError) as exc:
        result.update(reason="unsupported_zip", detail=str(exc))
    finally:
        if output_created and result["output_file"] is None:
            destination.unlink(missing_ok=True)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return result
