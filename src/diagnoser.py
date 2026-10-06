from zipfile import ZipFile


def diagnose_epub(epub_path: str, validation_result: dict) -> list[str]:
    """Inspect the ZIP to identify the cause of a PKG-006 message."""
    if not any(
        message["ID"] == "PKG-006"
        for message in validation_result["messages"]
    ):
        return []

    with ZipFile(epub_path) as archive:
        entries = archive.namelist()

    if "mimetype" not in entries:
        return ["mimetype_missing"]
    if entries[0] != "mimetype":
        return ["mimetype_not_first"]
    return []
