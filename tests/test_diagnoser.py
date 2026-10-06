from zipfile import BadZipFile, ZipFile

import pytest

from src.diagnoser import diagnose_epub
from src.validator import validate_epub


@pytest.mark.parametrize("mode,expected", [("missing", ["mimetype_missing"]), ("not_first", ["mimetype_not_first"]), ("valid", [])])
def test_real_diagnosis(make_epub, mode, expected):
    path = make_epub(mode)
    assert diagnose_epub(str(path), validate_epub(str(path))) == expected


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
