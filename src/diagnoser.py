from lzma import LZMAError
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_STORED, ZipFile
from zlib import error as ZlibError

from .manifest_mutation import add_required_manifest_properties
from .zip_inspection import inspect_local_headers


class _NoDTDTreeBuilder(ElementTree.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("DTD is not supported for diagnosis")


def _read_xml_bytes(archive: ZipFile, name: str):
    if archive.namelist().count(name) != 1:
        return None
    entry = archive.getinfo(name)
    # Bound decompression and parsing; oversized XML is outside this safe subset.
    limit = 8 * 1024 * 1024
    if entry.flag_bits & 1 or entry.is_dir() or entry.file_size > limit:
        return None
    with archive.open(entry) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        return None
    return data


def _read_xml(archive: ZipFile, name: str):
    data = _read_xml_bytes(archive, name)
    if data is None:
        return None
    return ElementTree.fromstring(
        data, parser=ElementTree.XMLParser(target=_NoDTDTreeBuilder())
    )


def _safe_package_path(path: str) -> bool:
    # Use exact ZIP names, never normalize or decode ambiguous URI/path forms.
    return bool(path) and not (
        any(part in ("", ".", "..") for part in path.split("/"))
        or any(char in path for char in "\\:%?#")
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in path)
    )


def _find_unique_package_path(archive: ZipFile, validation_result: dict) -> str | None:
    """Inspect a bounded .opf-only subset, not every possible package filename.

    Refuse unreadable/oversized/unsafe candidates rather than guessing uniqueness.
    At most 64 candidates and 32 MiB of declared XML are inspected in total.
    """
    message_ids = {message["ID"] for message in validation_result["messages"]}
    if "RSC-002" not in message_ids or message_ids.intersection({"PKG-005", "PKG-006", "PKG-007"}):
        return None
    publication = validation_result.get("publication", {})
    try:
        entries = archive.infolist()
        names = archive.namelist()
        if ("META-INF/container.xml" in names or len(names) != len(set(names))
                or names.count("mimetype") != 1
                or publication.get("hasSignatures") or publication.get("hasEncryption")
                or "META-INF/signatures.xml" in names or "META-INF/encryption.xml" in names
                or any(entry.flag_bits & 1 for entry in entries)):
            return None
        local_extras = inspect_local_headers(archive)
        mimetype = archive.getinfo("mimetype")
        if (names[0] != "mimetype" or mimetype.compress_type != ZIP_STORED
                or local_extras["mimetype"] or mimetype.extra
                or mimetype.file_size != len(b"application/epub+zip")
                or archive.read(mimetype) != b"application/epub+zip"):
            return None
        candidates = [entry for entry in entries if entry.filename.rstrip("/").lower().endswith(".opf")]
        if (not candidates or len(candidates) > 64
                or sum(entry.file_size for entry in candidates) > 32 * 1024 * 1024):
            return None
        package_path = None
        for entry in candidates:
            if (not _safe_package_path(entry.filename) or entry.is_dir()
                    or any(0x80 <= ord(char) <= 0x9f for char in entry.filename)):
                return None
            try:
                package = _read_xml(archive, entry.filename)
            except ElementTree.ParseError:
                # A malformed XML file is not an OPF package document.
                continue
            if package is None:
                return None
            if package.tag == "{http://www.idpf.org/2007/opf}package":
                if package_path is not None:
                    return None
                package_path = entry.filename
        return package_path
    except (ValueError, LookupError, BadZipFile, OSError, EOFError, RuntimeError,
            NotImplementedError, LZMAError, ZlibError):
        return None


def _container_rootfile(archive: ZipFile, *, reject_xml_base: bool = False):
    """Resolve the existing conservative single-rootfile OCF subset."""
    ocf = "{urn:oasis:names:tc:opendocument:xmlns:container}"
    container = _read_xml(archive, "META-INF/container.xml")
    if container is None or container.tag != ocf + "container":
        return None
    groups = container.findall(ocf + "rootfiles")
    if len(groups) != 1:
        return None
    candidates = list(groups[0])
    if (len(candidates) != 1 or candidates[0].tag != ocf + "rootfile"
            or len(list(container.iter(ocf + "rootfile"))) != 1):
        return None
    rootfile = candidates[0]
    if not _safe_package_path(rootfile.get("full-path", "")):
        return None
    if reject_xml_base and any("{http://www.w3.org/XML/1998/namespace}base" in element.attrib
                               for element in container.iter()):
        return None
    return rootfile


def _rootfile_incorrect_media_type(archive: ZipFile) -> bool:
    try:
        rootfile = _container_rootfile(archive)
        if rootfile is None:
            return False
        path = rootfile.get("full-path", "")
        if rootfile.get("media-type") == "application/oebps-package+xml":
            return False
        package = _read_xml(archive, path)
        return package is not None and package.tag == "{http://www.idpf.org/2007/opf}package"
    except (ElementTree.ParseError, ValueError, LookupError, BadZipFile, OSError,
            EOFError, RuntimeError, NotImplementedError, LZMAError, ZlibError):
        return False


def _manifest_property_replacement(archive: ZipFile, validation_result: dict):
    """Share complete OPF-014 evidence/mutation proof between diagnosis and repair."""
    if not any(message["ID"] == "OPF-014" for message in validation_result["messages"]):
        return None
    try:
        entries = archive.infolist()
        names = archive.namelist()
        publication = validation_result.get("publication", {})
        if (len(names) != len(set(names)) or names.count("mimetype") != 1
                or publication.get("hasSignatures") or publication.get("hasEncryption")
                or "META-INF/signatures.xml" in names or "META-INF/encryption.xml" in names
                or any(entry.flag_bits & 1 for entry in entries)):
            return None
        extras = inspect_local_headers(archive)
        mimetype = archive.getinfo("mimetype")
        if (names[0] != "mimetype" or mimetype.compress_type != ZIP_STORED
                or extras["mimetype"] or mimetype.extra
                or mimetype.file_size != len(b"application/epub+zip")
                or archive.read(mimetype) != b"application/epub+zip"
                or any(extras[entry.filename] or entry.extra for entry in entries)):
            return None
        rootfile = _container_rootfile(archive, reject_xml_base=True)
        if rootfile is None or rootfile.get("media-type") != "application/oebps-package+xml":
            return None
        package_path = rootfile.get("full-path")
        if any(127 <= ord(char) <= 159 for char in package_path):
            return None
        data = _read_xml_bytes(archive, package_path)
        if data is None:
            return None
        resources = {entry.filename for entry in entries if not entry.is_dir()}
        changed = add_required_manifest_properties(
            data, package_path, validation_result["messages"], resources
        )
        return package_path, changed
    except (ElementTree.ParseError, ValueError, LookupError, BadZipFile, OSError,
            EOFError, RuntimeError, NotImplementedError, LZMAError, ZlibError):
        return None


def diagnose_epub(epub_path: str, validation_result: dict) -> list[str]:
    """Confirm reported mimetype, container and conservative OPF-014 subsets."""
    message_ids = {message["ID"] for message in validation_result["messages"]}
    pkg_reported = message_ids.intersection({"PKG-005", "PKG-006", "PKG-007"})
    rootfile_reported = "RSC-003" in message_ids and not message_ids.intersection({"OPF-016", "OPF-017"})
    container_reported = "RSC-002" in message_ids and not pkg_reported
    manifest_reported = "OPF-014" in message_ids
    if not pkg_reported and not rootfile_reported and not container_reported and not manifest_reported:
        return []

    diagnoses = []
    try:
        archive = ZipFile(epub_path)
    except (BadZipFile, OSError):
        if pkg_reported:
            raise
        return []
    with archive:
        entries = archive.namelist()
        if "PKG-006" in message_ids:
            if "mimetype" not in entries:
                diagnoses.append("mimetype_missing")
            elif entries[0] != "mimetype":
                diagnoses.append("mimetype_not_first")
        if "PKG-007" in message_ids and entries.count("mimetype") == 1:
            entry = archive.getinfo("mimetype")
            # Duplicated, absent, or encrypted entries are not unambiguous repairs.
            if not (entry.flag_bits & 1) and archive.read(entry) != b"application/epub+zip":
                diagnoses.append("mimetype_incorrect_content")
        if "PKG-005" in message_ids and entries.count("mimetype") == 1:
            if inspect_local_headers(archive)["mimetype"]:
                diagnoses.append("mimetype_has_extra_field")
        if rootfile_reported and _rootfile_incorrect_media_type(archive):
            diagnoses.append("rootfile_incorrect_media_type")
        if container_reported and _find_unique_package_path(archive, validation_result) is not None:
            diagnoses.append("container_xml_missing")
        if manifest_reported and _manifest_property_replacement(archive, validation_result) is not None:
            diagnoses.append("manifest_missing_required_property")
    return diagnoses
