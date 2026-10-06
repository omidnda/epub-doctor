from zipfile import ZipFile

from .zip_inspection import inspect_local_headers


def diagnose_epub(epub_path: str, validation_result: dict) -> list[str]:
    """Confirm mimetype diagnoses for reported PKG-005/PKG-006/PKG-007 messages."""
    message_ids = {message["ID"] for message in validation_result["messages"]}
    if not message_ids.intersection({"PKG-005", "PKG-006", "PKG-007"}):
        return []

    diagnoses = []
    with ZipFile(epub_path) as archive:
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
    return diagnoses
