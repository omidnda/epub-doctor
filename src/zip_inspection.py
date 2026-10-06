"""Conservative checks for ZIP headers used by mimetype repairs, not EPUB validation."""

import struct
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_LZMA, ZIP_STORED, ZIP64_LIMIT, ZipFile


class UnsafeZipError(ValueError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason


def _check_extra(extra: bytes) -> None:
    offset = 0
    while offset < len(extra):
        if len(extra) - offset < 4:
            raise UnsafeZipError("invalid_zip_structure", "Truncated ZIP extra field")
        tag, size = struct.unpack_from("<HH", extra, offset)
        offset += 4
        if offset + size > len(extra):
            raise UnsafeZipError("invalid_zip_structure", "ZIP extra field exceeds its bounds")
        if tag == 1:
            raise UnsafeZipError("unsupported_zip64", "ZIP64 extra fields are not supported")
        offset += size


def inspect_local_headers(archive: ZipFile) -> dict[str, bytes]:
    """Return local extra fields only after confirming a simple, consistent ZIP.

    Reject ZIP64, descriptors, split/prefixed archives, gaps, overlapping records,
    and local/central conflicts. Unknown filename encodings are never guessed.
    """
    stream = archive.fp
    saved_position = stream.tell()
    try:
        stream.seek(0, 2)
        file_size = stream.tell()
        end_offset = file_size - 22 - len(archive.comment)
        if end_offset < 0:
            raise UnsafeZipError("invalid_zip_structure", "Truncated ZIP end record")
        stream.seek(end_offset)
        end_record = stream.read(22)
        if len(end_record) != 22:
            raise UnsafeZipError("invalid_zip_structure", "Truncated ZIP end record")
        signature, disk, central_disk, disk_count, count, central_size, central_offset, comment_size = struct.unpack(
            "<4s4H2IH", end_record
        )
        if signature != b"PK\x05\x06" or comment_size != len(archive.comment):
            raise UnsafeZipError("invalid_zip_structure", "Invalid ZIP end record")
        if count == 0xffff or central_size == 0xffffffff or central_offset == 0xffffffff:
            raise UnsafeZipError("unsupported_zip64", "ZIP64 end records are not supported")
        entries = archive.infolist()
        if len({entry.filename for entry in entries}) != len(entries):
            raise UnsafeZipError("duplicate_zip_entries", "Duplicate ZIP entries")
        if (disk or central_disk or disk_count != count or count != len(entries)
                or central_offset != archive.start_dir
                or central_offset + central_size != end_offset):
            raise UnsafeZipError("invalid_zip_structure", "Ambiguous ZIP directory layout")
        if central_offset >= ZIP64_LIMIT:
            raise UnsafeZipError("unsupported_zip64", "ZIP would require ZIP64 rebuilding")
        offsets = [entry.header_offset for entry in entries]
        if offsets != sorted(set(offsets)) or (offsets and offsets[0] != 0):
            raise UnsafeZipError("invalid_zip_structure", "Ambiguous local entry offsets/order")
        extras = {}
        for index, entry in enumerate(entries):
            if entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED, ZIP_BZIP2, ZIP_LZMA):
                raise NotImplementedError(f"Unsupported ZIP compression: {entry.compress_type}")
            if (entry.extract_version == 45 or entry.file_size >= ZIP64_LIMIT
                    or entry.compress_size >= ZIP64_LIMIT):
                raise UnsafeZipError("unsupported_zip64", "ZIP64 entries are not supported")
            if entry.flag_bits & 8:
                raise UnsafeZipError("unsupported_zip_structure", "ZIP data descriptors are not supported")
            # Only UTF-8 and compression option bits are supported by this rebuild.
            if entry.flag_bits & ~0x806:
                raise UnsafeZipError("unsupported_zip_structure", "Unsupported ZIP entry flags")
            limit = offsets[index + 1] if index + 1 < len(offsets) else central_offset
            offset = entry.header_offset
            if offset < 0 or offset + 30 > limit or limit > central_offset:
                raise UnsafeZipError("invalid_zip_structure", "Local ZIP header exceeds its bounds")
            stream.seek(offset)
            header = stream.read(30)
            if len(header) != 30:
                raise UnsafeZipError("invalid_zip_structure", "Truncated local ZIP header")
            sig, version, flags, method, time, date, crc, compressed, size, name_length, extra_length = struct.unpack(
                "<4s5H3I2H", header
            )
            if sig != b"PK\x03\x04":
                raise UnsafeZipError("invalid_zip_structure", "Invalid local ZIP header signature")
            if compressed == 0xffffffff or size == 0xffffffff:
                raise UnsafeZipError("unsupported_zip64", "ZIP64 local sizes are not supported")
            data_offset = offset + 30 + name_length + extra_length
            if data_offset > limit or data_offset + compressed != limit:
                raise UnsafeZipError("invalid_zip_structure", "Truncated, overlapping, or gapped local record")
            raw_name = stream.read(name_length)
            extra = stream.read(extra_length)
            if len(raw_name) != name_length or len(extra) != extra_length:
                raise UnsafeZipError("invalid_zip_structure", "Truncated ZIP name or extra field")
            try:
                name = raw_name.decode("utf-8" if flags & 0x800 else "ascii")
            except UnicodeError as exc:
                raise UnsafeZipError("invalid_zip_structure", "Uncertain ZIP filename encoding") from exc
            year, month, day, hour, minute, second = entry.date_time
            expected_date = ((year - 1980) << 9) | (month << 5) | day
            expected_time = (hour << 11) | (minute << 5) | (second // 2)
            if (name != entry.orig_filename or name != entry.filename or "\x00" in name
                    or flags != entry.flag_bits or method != entry.compress_type
                    or version != entry.extract_version or entry.reserved
                    or crc != entry.CRC or compressed != entry.compress_size or size != entry.file_size
                    or date != expected_date or time != expected_time):
                raise UnsafeZipError("invalid_zip_structure", "Conflicting local and central ZIP metadata")
            _check_extra(extra)
            _check_extra(entry.extra)
            extras[entry.filename] = extra
        return extras
    finally:
        stream.seek(saved_position)
