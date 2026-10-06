from zipfile import BadZipFile, ZipFile

import pytest

from src.diagnoser import diagnose_epub
from src.validator import validate_epub


@pytest.mark.parametrize("mode,expected", [("missing", ["mimetype_missing"]), ("not_first", ["mimetype_not_first"]), ("valid", []), ("incorrect", ["mimetype_incorrect_content"]), ("incorrect_not_first", ["mimetype_not_first", "mimetype_incorrect_content"])])
def test_real_diagnosis(make_epub, mode, expected):
    path = make_epub(mode)
    report = validate_epub(str(path))
    if "mimetype_incorrect_content" in expected:
        assert any(message["ID"] == "PKG-007" for message in report["messages"])
    assert diagnose_epub(str(path), report) == expected


def test_no_pkg006_does_not_open_zip(tmp_path):
    assert diagnose_epub(str(tmp_path / "absent"), {"messages": [{"ID": "PKG-003"}]}) == []


def test_empty_zip(tmp_path):
    path = tmp_path / "empty.epub"
    with ZipFile(path, "w"):
        pass
    assert diagnose_epub(str(path), {"messages": [{"ID": "PKG-006"}]}) == ["mimetype_missing"]


def test_invalid_zip(tmp_path):
    path = tmp_path / "broken.epub"
    path.write_bytes(b"not zip")
    with pytest.raises(BadZipFile):
        diagnose_epub(str(path), {"messages": [{"ID": "PKG-006"}]})


def test_nested_mimetype_is_not_root_entry(tmp_path):
    path = tmp_path / "nested.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr("folder/mimetype", b"application/epub+zip")
    assert diagnose_epub(str(path), {"messages": [{"ID": "PKG-006"}]}) == ["mimetype_missing"]


@pytest.mark.parametrize("messages", [[], [{"ID": "PKG-003"}], [{"ID": "PKG-006"}]])
def test_wrong_content_requires_pkg007(make_epub, messages):
    path = make_epub("incorrect")
    assert diagnose_epub(str(path), {"messages": messages}) == []


@pytest.mark.parametrize("mode", ["valid", "missing"])
def test_pkg007_requires_existing_incorrect_root_entry(make_epub, mode):
    path = make_epub(mode)
    assert diagnose_epub(str(path), {"messages": [{"ID": "PKG-007"}]}) == []


def test_pkg007_duplicate_mimetype_is_ambiguous(make_epub):
    path = make_epub("incorrect")
    with pytest.warns(UserWarning), ZipFile(path, "a") as archive:
        archive.writestr("mimetype", b"another wrong value")
    assert diagnose_epub(str(path), {"messages": [{"ID": "PKG-007"}]}) == []


def test_pkg007_encrypted_entry_is_not_read(monkeypatch, make_epub):
    path = make_epub("incorrect")
    original_getinfo = ZipFile.getinfo
    def encrypted_getinfo(archive, name):
        entry = original_getinfo(archive, name)
        if name == "mimetype":
            entry.flag_bits |= 1
        return entry
    def forbidden_read(*args, **kwargs):
        raise AssertionError("Encrypted mimetype must not be read")
    monkeypatch.setattr(ZipFile, "getinfo", encrypted_getinfo)
    monkeypatch.setattr(ZipFile, "read", forbidden_read)
    assert diagnose_epub(str(path), {"messages": [{"ID": "PKG-007"}]}) == []
