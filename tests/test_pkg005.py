import struct
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from src.diagnoser import diagnose_epub
from src.repairer import repair_epub
from src import repairer
from src.validator import validate_epub
from src.zip_inspection import UnsafeZipError, inspect_local_headers


EXTRA = struct.pack("<HHBI", 0x5455, 5, 1, 1700000000)
REPORT = {"valid": False, "messages": [{"ID": "PKG-005"}], "publication": {}}


def central_record(path, name):
    data = path.read_bytes()
    with ZipFile(path) as archive:
        offset = archive.start_dir
        for entry in archive.infolist():
            name_size, extra_size, comment_size = struct.unpack_from("<HHH", data, offset + 28)
            if entry.filename == name:
                return offset, offset + 46 + name_size, extra_size
            offset += 46 + name_size + extra_size + comment_size
    raise AssertionError(name)


def replace_central_extra(path, name, extra):
    data = bytearray(path.read_bytes())
    header, start, old_size = central_record(path, name)
    with ZipFile(path) as archive:
        end_offset = len(data) - 22 - len(archive.comment)
    delta = len(extra) - old_size
    struct.pack_into("<H", data, header + 30, len(extra))
    old_central_size = struct.unpack_from("<I", data, end_offset + 12)[0]
    struct.pack_into("<I", data, end_offset + 12, old_central_size + delta)
    data[start:start + old_size] = extra
    path.write_bytes(data)


def original_report(monkeypatch):
    monkeypatch.setattr(repairer, "validate_epub", lambda path: REPORT)


@pytest.mark.parametrize("mode,expected", [
    ("extra", ["mimetype_has_extra_field"]),
    ("extra_incorrect", ["mimetype_incorrect_content", "mimetype_has_extra_field"]),
])
def test_real_pkg005_repair(make_epub, tmp_path, mode, expected):
    source = make_epub(mode)
    # Exercise different mimetype local/central extras, which are both discarded.
    replace_central_extra(source, "mimetype", b"")
    original = source.read_bytes()
    report = validate_epub(str(source))
    assert any(message["ID"] == "PKG-005" for message in report["messages"])
    assert diagnose_epub(str(source), report) == expected
    output = tmp_path / "repaired.epub"
    result = repair_epub(str(source), str(output))
    assert result["final_valid"] is True, result
    assert result["detected"] == result["repaired"] == expected
    assert result["output_file"] == str(output)
    assert result["remaining_messages"] == []
    assert source.read_bytes() == original
    with ZipFile(source) as before, ZipFile(output) as after:
        assert after.namelist()[0] == "mimetype"
        assert after.namelist().count("mimetype") == 1
        mimetype = after.getinfo("mimetype")
        assert mimetype.header_offset == 0
        assert mimetype.compress_type == ZIP_STORED
        assert mimetype.extra == b""
        assert after.read(mimetype) == b"application/epub+zip"
        # Independently inspect the output's physical length field.
        assert struct.unpack_from("<H", output.read_bytes(), 28)[0] == 0
        for entry in before.infolist():
            if entry.filename != "mimetype":
                assert before.read(entry) == after.read(entry.filename)
                assert entry.compress_type == after.getinfo(entry.filename).compress_type
    final = validate_epub(str(output))
    assert final["valid"] and final["warning_count"] == 0


@pytest.mark.parametrize("messages", [[], [{"ID": "PKG-006"}], [{"ID": "PKG-007"}]])
def test_extra_requires_pkg005(make_epub, messages):
    assert diagnose_epub(str(make_epub("extra")), {"messages": messages}) == []


@pytest.mark.parametrize("mode", ["valid", "missing"])
def test_pkg005_requires_local_confirmation(make_epub, mode):
    assert diagnose_epub(str(make_epub(mode)), REPORT) == []


def test_central_only_extra_is_not_local_confirmation(make_epub):
    source = make_epub()
    replace_central_extra(source, "mimetype", EXTRA)
    assert diagnose_epub(str(source), REPORT) == []


@pytest.mark.parametrize("mutation", ["signature", "name", "crc", "method", "flags", "time", "version", "name_length", "extra_length", "zip64", "extra_bounds"])
def test_malformed_or_conflicting_local_header(monkeypatch, make_epub, tmp_path, mutation):
    source = make_epub("extra")
    data = bytearray(source.read_bytes())
    if mutation == "signature":
        data[0:4] = b"BAD!"
    elif mutation == "name":
        data[30:38] = b"wrongone"
    elif mutation == "crc":
        struct.pack_into("<I", data, 14, 0)
    elif mutation == "method":
        struct.pack_into("<H", data, 8, ZIP_DEFLATED)
    elif mutation == "flags":
        struct.pack_into("<H", data, 6, 0x800)
    elif mutation == "time":
        struct.pack_into("<H", data, 10, 1)
    elif mutation == "version":
        struct.pack_into("<H", data, 4, 21)
    elif mutation == "name_length":
        struct.pack_into("<H", data, 26, 65535)
    elif mutation == "extra_length":
        struct.pack_into("<H", data, 28, 65535)
    elif mutation == "zip64":
        struct.pack_into("<I", data, 18, 0xffffffff)
    elif mutation == "extra_bounds":
        struct.pack_into("<H", data, 40, 65535)
    source.write_bytes(data)
    original = source.read_bytes()
    original_report(monkeypatch)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == ("unsupported_zip64" if mutation == "zip64" else "invalid_zip_structure")
    assert result["repaired"] == [] and result["output_file"] is None
    assert source.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize("offset", [-1, 1, 10**9])
def test_invalid_header_offset(make_epub, offset):
    with ZipFile(make_epub("extra")) as archive:
        archive.getinfo("mimetype").header_offset = offset
        with pytest.raises(UnsafeZipError, match="offset|bounds|layout"):
            inspect_local_headers(archive)


def test_truncated_input(monkeypatch, make_epub, tmp_path):
    source = make_epub("extra")
    source.write_bytes(source.read_bytes()[:29])
    original_report(monkeypatch)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "invalid_zip"
    assert result["output_file"] is None
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize("location", ["both", "local_only", "central_only"])
def test_non_mimetype_extras_are_refused(monkeypatch, make_epub, tmp_path, location):
    source = make_epub("extra")
    with ZipFile(source, "a") as archive:
        entry = ZipInfo("EPUB/extra-data.txt")
        if location != "central_only":
            entry.extra = EXTRA
        archive.writestr(entry, b"keep")
    if location == "local_only":
        replace_central_extra(source, "EPUB/extra-data.txt", b"")
    elif location == "central_only":
        replace_central_extra(source, "EPUB/extra-data.txt", EXTRA)
    original = source.read_bytes()
    original_report(monkeypatch)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["detected"] == ["mimetype_has_extra_field"]
    assert result["reason"] == "unsafe_metadata_preservation"
    assert result["repaired"] == [] and result["output_file"] is None
    assert source.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize("external_attr", [0, 0o100640 << 16])
def test_metadata_preserved(make_epub, tmp_path, external_attr):
    source = make_epub("extra")
    with ZipFile(source, "a") as archive:
        archive.comment = b"Archive comment"
        entry = ZipInfo("EPUB/additional.txt", date_time=(2020, 2, 3, 4, 5, 6))
        entry.compress_type = ZIP_DEFLATED
        entry.comment = b"Entry comment"
        entry.internal_attr = 1
        entry.external_attr = external_attr
        entry.create_system = 3
        archive.writestr(entry, b"Additional bytes" * 20)
    # zipfile's write defaults normalize zero attributes; test the true zero case.
    header, _, _ = central_record(source, "EPUB/additional.txt")
    data = bytearray(source.read_bytes())
    struct.pack_into("<I", data, header + 38, external_attr)
    source.write_bytes(data)
    original = source.read_bytes()
    output = tmp_path / "out.epub"
    result = repair_epub(str(source), str(output))
    assert result["final_valid"] is True, result
    assert source.read_bytes() == original
    with ZipFile(source) as before, ZipFile(output) as after:
        assert before.comment == after.comment
        for entry in before.infolist():
            if entry.filename == "mimetype":
                continue
            copied = after.getinfo(entry.filename)
            assert before.read(entry) == after.read(copied)
            for attribute in ("date_time", "compress_type", "comment", "extra", "internal_attr",
                              "external_attr", "create_system", "create_version", "extract_version"):
                assert getattr(entry, attribute) == getattr(copied, attribute), attribute


@pytest.mark.parametrize("protection", ["signatures", "encryption", "duplicate"])
def test_safety_refusals(monkeypatch, make_epub, tmp_path, protection):
    source = make_epub("extra")
    if protection == "duplicate":
        with pytest.warns(UserWarning), ZipFile(source, "a") as archive:
            archive.writestr("mimetype", b"application/epub+zip")
    else:
        with ZipFile(source, "a") as archive:
            archive.writestr(f"META-INF/{protection}.xml", b"")
    original = source.read_bytes()
    original_report(monkeypatch)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    expected = "duplicate_zip_entries" if protection == "duplicate" else "signatures_or_encryption"
    assert result["reason"] == expected
    assert result["repaired"] == [] and result["output_file"] is None
    assert source.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source}


def test_combined_diagnoses_use_one_rebuild(monkeypatch, make_epub, tmp_path):
    source = make_epub("extra_incorrect_not_first")
    expected = ["mimetype_not_first", "mimetype_incorrect_content", "mimetype_has_extra_field"]
    report = {"valid": False, "messages": [{"ID": code} for code in ("PKG-005", "PKG-006", "PKG-007")], "publication": {}}
    calls = []
    def validate(path):
        calls.append(path)
        return report if len(calls) == 1 else validate_epub(path)
    monkeypatch.setattr(repairer, "validate_epub", validate)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["detected"] == result["repaired"] == expected
    assert result["final_valid"] is True
    assert len(calls) == 2


def test_real_revalidation_failure_does_not_publish(make_epub, tmp_path):
    source = make_epub("extra")
    with ZipFile(source) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
    with ZipFile(source, "w") as archive:
        for entry, data in entries:
            archive.writestr(entry, b"<broken>" if entry.filename == "EPUB/chapter.xhtml" else data)
    original = source.read_bytes()
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["detected"] == ["mimetype_has_extra_field"]
    assert result["reason"] == "revalidation_failed"
    assert result["final_valid"] is False
    assert result["remaining_messages"]
    assert all(message["ID"] != "PKG-005" for message in result["remaining_messages"])
    assert result["repaired"] == [] and result["output_file"] is None
    assert source.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source}


def test_zip64_is_refused(monkeypatch, make_epub, tmp_path):
    source = make_epub("extra")
    with ZipFile(source, "a") as archive:
        entry = ZipInfo("EPUB/zip64.txt")
        with archive.open(entry, "w", force_zip64=True) as output:
            output.write(b"small payload, but ZIP64 header")
    original_report(monkeypatch)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "unsupported_zip64"
    assert result["repaired"] == [] and result["output_file"] is None
    assert set(tmp_path.iterdir()) == {source}


def test_data_descriptor_is_refused(monkeypatch, make_epub, tmp_path):
    import io
    source = make_epub("extra")
    with ZipFile(source) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
    buffer = io.BytesIO()
    class WriteOnly:
        def write(self, data):
            return buffer.write(data)
        def flush(self):
            pass
    with ZipFile(WriteOnly(), "w") as archive:
        for entry, data in entries:
            archive.writestr(entry, data)
    source.write_bytes(buffer.getvalue())
    original_report(monkeypatch)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "unsupported_zip_structure"
    assert result["repaired"] == [] and result["output_file"] is None
    assert set(tmp_path.iterdir()) == {source}


def test_encrypted_zip_flags_are_refused(monkeypatch, make_epub, tmp_path):
    source = make_epub("extra")
    data = bytearray(source.read_bytes())
    header, _, _ = central_record(source, "mimetype")
    struct.pack_into("<H", data, 6, 1)
    struct.pack_into("<H", data, header + 8, 1)
    source.write_bytes(data)
    original_report(monkeypatch)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "signatures_or_encryption"
    assert result["output_file"] is None
    assert set(tmp_path.iterdir()) == {source}


def test_destination_guards(monkeypatch, make_epub, tmp_path):
    source = make_epub("extra")
    original_report(monkeypatch)
    assert repair_epub(str(source), str(source))["reason"] == "output_is_input"
    output = tmp_path / "existing.epub"
    output.write_bytes(b"keep destination")
    assert repair_epub(str(source), str(output))["reason"] == "output_exists"
    assert output.read_bytes() == b"keep destination"


def test_short_read_of_local_header(make_epub, monkeypatch):
    with ZipFile(make_epub("extra")) as archive:
        original_read = archive.fp.read
        def short_read(size=-1):
            data = original_read(size)
            return data[:-1] if size == 30 else data
        monkeypatch.setattr(archive.fp, "read", short_read)
        with pytest.raises(UnsafeZipError, match="Truncated local"):
            inspect_local_headers(archive)


def test_duplicate_mimetype_not_diagnosed(make_epub):
    source = make_epub("extra")
    with pytest.warns(UserWarning), ZipFile(source, "a") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
    assert diagnose_epub(str(source), REPORT) == []


def test_corrupt_compressed_payload_is_refused(monkeypatch, make_epub, tmp_path):
    source = make_epub("extra")
    with ZipFile(source) as archive:
        entries = [(entry, archive.read(entry)) for entry in archive.infolist()]
    with ZipFile(source, "w") as archive:
        for entry, data in entries:
            archive.writestr(entry, data, compress_type=ZIP_STORED if entry.filename == "mimetype" else ZIP_DEFLATED)
    with ZipFile(source) as archive:
        offset = archive.getinfo("META-INF/container.xml").header_offset
    data = bytearray(source.read_bytes())
    name_size, extra_size = struct.unpack_from("<HH", data, offset + 26)
    # DEFLATE block type 3 is invalid. Keep header/central metadata unchanged.
    data[offset + 30 + name_size + extra_size] = 0xff
    source.write_bytes(data)
    original = source.read_bytes()
    original_report(monkeypatch)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "invalid_zip"
    assert result["output_file"] is None and result["repaired"] == []
    assert source.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source}


def test_metadata_normalization_is_refused(monkeypatch, make_epub, tmp_path):
    from zipfile import ZIP_BZIP2
    source = make_epub("extra")
    with ZipFile(source, "a") as archive:
        archive.writestr("EPUB/bzip2.txt", b"keep", compress_type=ZIP_BZIP2)
    header, _, _ = central_record(source, "EPUB/bzip2.txt")
    data = bytearray(source.read_bytes())
    # The source creator version differs from zipfile's BZIP2 write minimum.
    data[header + 4] = 20
    source.write_bytes(data)
    original = source.read_bytes()
    original_report(monkeypatch)
    result = repair_epub(str(source), str(tmp_path / "out.epub"))
    assert result["reason"] == "unsafe_metadata_preservation"
    assert result["repaired"] == [] and result["output_file"] is None
    assert set(tmp_path.iterdir()) == {source}
    assert source.read_bytes() == original
