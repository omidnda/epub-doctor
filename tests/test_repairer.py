from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_LZMA, ZIP_STORED, ZipFile

import pytest

from src import repairer
from src.validator import EPUBCheckError


@pytest.mark.parametrize("mode,diagnosis", [("missing", "mimetype_missing"), ("not_first", "mimetype_not_first")])
def test_real_repair(make_epub, tmp_path, mode, diagnosis):
    source = make_epub(mode)
    original = source.read_bytes()
    output = tmp_path / "repaired.epub"
    result = repairer.repair_epub(str(source), str(output))
    assert result["original_valid"] is False
    assert result["final_valid"] is True, result
    assert result["detected"] == result["repaired"] == [diagnosis]
    assert result["remaining_messages"] == []
    assert source.read_bytes() == original
    with ZipFile(source) as before, ZipFile(output) as after:
        assert after.namelist()[0] == "mimetype"
        assert after.namelist().count("mimetype") == 1
        assert after.read("mimetype") == b"application/epub+zip"
        assert after.getinfo("mimetype").compress_type == ZIP_STORED
        for entry in before.infolist():
            assert before.read(entry) == after.read(entry.filename)


def stub(monkeypatch, publication=None):
    original = {"valid": False, "messages": [{"ID": "PKG-006"}],
                "publication": publication or {}}
    monkeypatch.setattr(repairer, "validate_epub", lambda path: original)


@pytest.mark.parametrize("flag", ["hasSignatures", "hasEncryption"])
def test_metadata_protection(monkeypatch, make_epub, tmp_path, flag):
    source = make_epub("missing")
    stub(monkeypatch, {flag: True})
    result = repairer.repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "signatures_or_encryption"
    assert result["output_file"] is None


@pytest.mark.parametrize("name", ["META-INF/signatures.xml", "META-INF/encryption.xml"])
def test_archive_protection(monkeypatch, make_epub, tmp_path, name):
    source = make_epub("missing", {name: ""})
    stub(monkeypatch)
    assert repairer.repair_epub(str(source), str(tmp_path / "out.epub"))["reason"] == "signatures_or_encryption"


def test_existing_output_and_same_path(monkeypatch, make_epub, tmp_path):
    source = make_epub("missing")
    stub(monkeypatch)
    assert repairer.repair_epub(str(source), str(source))["reason"] == "output_is_input"
    output = tmp_path / "out.epub"
    output.write_bytes(b"keep")
    assert repairer.repair_epub(str(source), str(output))["reason"] == "output_exists"
    assert output.read_bytes() == b"keep"


def test_output_write_failure(monkeypatch, make_epub, tmp_path):
    source = make_epub("missing")
    stub(monkeypatch)
    result = repairer.repair_epub(str(source), str(tmp_path / "absent" / "out.epub"))
    assert result["reason"] == "output_write_failed"
    assert result["repaired"] == []


def test_revalidation_failure(monkeypatch, make_epub, tmp_path):
    source = make_epub("missing")
    reports = iter([
        {"valid": False, "messages": [{"ID": "PKG-006"}], "publication": {}},
        {"valid": False, "messages": [{"ID": "OTHER"}], "publication": {}},
    ])
    monkeypatch.setattr(repairer, "validate_epub", lambda path: next(reports))
    result = repairer.repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "revalidation_failed"
    assert result["repaired"] == []
    assert result["final_valid"] is False
    assert result["remaining_messages"] == [{"ID": "OTHER"}]
    assert result["output_file"] is None
    assert set(tmp_path.iterdir()) == {source}


def test_revalidation_execution_failure(monkeypatch, make_epub, tmp_path):
    source = make_epub("missing")
    calls = 0
    def validate(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise EPUBCheckError("failed")
        return {"valid": False, "messages": [{"ID": "PKG-006"}], "publication": {}}
    monkeypatch.setattr(repairer, "validate_epub", validate)
    output = tmp_path / "out.epub"
    result = repairer.repair_epub(str(source), str(output))
    assert result["reason"] == "epubcheck_failure"
    assert result["final_valid"] is None
    assert not output.exists()
    assert set(tmp_path.iterdir()) == {source}


def test_invalid_and_missing_input(tmp_path):
    output = tmp_path / "out.epub"
    result = repairer.repair_epub(str(tmp_path / "absent"), str(output))
    assert result["reason"] == "input_read_failed"
    source = tmp_path / "invalid.epub"
    source.write_bytes(b"not a zip")
    assert repairer.repair_epub(str(source), str(output))["reason"] == "invalid_zip"
    assert not output.exists()


def test_already_valid(make_epub, tmp_path):
    result = repairer.repair_epub(str(make_epub()), str(tmp_path / "out.epub"))
    assert result["reason"] == "already_valid"
    assert result["final_valid"] is True
    assert result["output_file"] is None


def test_duplicate_entries(monkeypatch, make_epub, tmp_path):
    source = make_epub("not_first")
    with pytest.warns(UserWarning), ZipFile(source, "a") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
    stub(monkeypatch)
    assert repairer.repair_epub(str(source), str(tmp_path / "out.epub"))["reason"] == "duplicate_zip_entries"


def test_wrong_mimetype_content(monkeypatch, make_epub, tmp_path):
    source = make_epub("missing", {"mimetype": b"wrong"})
    stub(monkeypatch)
    assert repairer.repair_epub(str(source), str(tmp_path / "out.epub"))["reason"] == "unsupported_mimetype_content"


def test_no_supported_repair(monkeypatch, make_epub, tmp_path):
    monkeypatch.setattr(repairer, "validate_epub", lambda path: {"valid": False, "messages": [{"ID": "OTHER"}], "publication": {}})
    result = repairer.repair_epub(str(make_epub()), str(tmp_path / "out.epub"))
    assert result["reason"] == "no_supported_repair"


def test_archive_paths_never_extracted(monkeypatch, make_epub, tmp_path):
    source = make_epub("missing", {"../escaped.txt": b"preserved"})
    reports = iter([
        {"valid": False, "messages": [{"ID": "PKG-006"}], "publication": {}},
        {"valid": True, "messages": [], "publication": {}},
    ])
    monkeypatch.setattr(repairer, "validate_epub", lambda path: next(reports))
    output = tmp_path / "out.epub"
    repairer.repair_epub(str(source), str(output))
    assert set(tmp_path.iterdir()) == {source, output}
    with ZipFile(output) as archive:
        assert archive.read("../escaped.txt") == b"preserved"


def test_publication_failure_cleans_temporary(monkeypatch, make_epub, tmp_path):
    source = make_epub("missing")
    reports = iter([
        {"valid": False, "messages": [{"ID": "PKG-006"}], "publication": {}},
        {"valid": True, "messages": [], "publication": {}},
    ])
    monkeypatch.setattr(repairer, "validate_epub", lambda path: next(reports))
    def fail(validated, output):
        output.write(b"partial")
        raise PermissionError("output denied")
    monkeypatch.setattr(repairer.shutil, "copyfileobj", fail)
    result = repairer.repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "output_write_failed"
    assert result["repaired"] == []
    assert result["output_file"] is None
    assert set(tmp_path.iterdir()) == {source}


def test_real_remaining_errors(make_epub, tmp_path):
    source = make_epub("missing")
    # Damage another part: PKG-006 repair must not claim full success.
    with ZipFile(source) as archive:
        entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
    with ZipFile(source, "w") as archive:
        for name, data in entries:
            archive.writestr(name, b"<broken>" if name == "EPUB/chapter.xhtml" else data)
    result = repairer.repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["detected"] == ["mimetype_missing"]
    assert result["reason"] == "revalidation_failed"
    assert result["final_valid"] is False
    assert result["repaired"] == []
    assert result["remaining_messages"]
    assert all(msg["ID"] != "PKG-006" for msg in result["remaining_messages"])
    assert result["output_file"] is None
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize("compression", [ZIP_STORED, ZIP_DEFLATED, ZIP_BZIP2, ZIP_LZMA])
def test_preserves_compression(monkeypatch, make_epub, tmp_path, compression):
    source = make_epub("missing")
    with ZipFile(source) as archive:
        entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
    with ZipFile(source, "w") as archive:
        for index, (name, data) in enumerate(entries):
            archive.writestr(name, data, compress_type=compression if index % 2 else ZIP_STORED)
    original = source.read_bytes()
    reports = iter([
        {"valid": False, "messages": [{"ID": "PKG-006"}], "publication": {}},
        {"valid": True, "messages": [], "publication": {}},
    ])
    monkeypatch.setattr(repairer, "validate_epub", lambda path: next(reports))
    output = tmp_path / "out.epub"
    result = repairer.repair_epub(str(source), str(output))
    assert result["repaired"] == ["mimetype_missing"]
    assert source.read_bytes() == original
    with ZipFile(source) as before, ZipFile(output) as after:
        assert after.namelist()[0] == "mimetype"
        assert after.read("mimetype") == b"application/epub+zip"
        assert after.getinfo("mimetype").compress_type == ZIP_STORED
        for entry in before.infolist():
            assert after.getinfo(entry.filename).compress_type == entry.compress_type
            assert after.read(entry.filename) == before.read(entry)


@pytest.mark.parametrize("symlink", [False, True])
def test_publication_race_preserves_existing_file(monkeypatch, make_epub, tmp_path, symlink):
    source = make_epub("missing")
    original = source.read_bytes()
    output = tmp_path / "out.epub"
    def validate(path):
        if path == str(source):
            return {"valid": False, "messages": [{"ID": "PKG-006"}], "publication": {}}
        # Simulate another writer creating the destination after the precheck.
        if symlink:
            output.symlink_to(source)
        else:
            output.write_bytes(b"another writer")
        return {"valid": True, "messages": [], "publication": {}}
    monkeypatch.setattr(repairer, "validate_epub", validate)
    result = repairer.repair_epub(str(source), str(output))
    assert result["reason"] == "output_write_failed"
    assert result["output_file"] is None
    assert result["repaired"] == []
    assert source.read_bytes() == original
    assert output.is_symlink() if symlink else output.read_bytes() == b"another writer"
    assert set(tmp_path.iterdir()) == {source, output}


def test_publication_without_hard_links(monkeypatch, make_epub, tmp_path):
    import os
    def no_links(*args, **kwargs):
        raise OSError("Hard links unavailable")
    monkeypatch.setattr(os, "link", no_links)
    source = make_epub("missing")
    reports = iter([
        {"valid": False, "messages": [{"ID": "PKG-006"}], "publication": {}},
        {"valid": True, "messages": [], "publication": {}},
    ])
    monkeypatch.setattr(repairer, "validate_epub", lambda path: next(reports))
    output = tmp_path / "out.epub"
    result = repairer.repair_epub(str(source), str(output))
    assert result["reason"] is None
    assert result["output_file"] == str(output)
    assert result["repaired"] == ["mimetype_missing"]
    assert set(tmp_path.iterdir()) == {source, output}


def test_unsupported_compression_is_not_converted(monkeypatch, make_epub, tmp_path):
    source = make_epub("missing")
    stub(monkeypatch)
    original_infolist = ZipFile.infolist
    def unsupported_infolist(archive):
        entries = original_infolist(archive)
        entries[0].compress_type = 99
        return entries
    monkeypatch.setattr(ZipFile, "infolist", unsupported_infolist)
    result = repairer.repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "unsupported_zip"
    assert result["repaired"] == []
    assert result["output_file"] is None
    assert set(tmp_path.iterdir()) == {source}
