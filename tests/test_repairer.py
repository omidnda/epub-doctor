from zipfile import ZIP_STORED, ZipFile

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
    def fail(*args):
        raise PermissionError("output denied")
    monkeypatch.setattr(repairer.os, "link", fail)
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
