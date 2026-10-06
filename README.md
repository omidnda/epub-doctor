# EPUB Doctor

Small Python engine for deterministic repairs. Bundled EPUBCheck 5.4.0 is the
validation authority; Python diagnoses its reports rather than validating EPUB
content independently. Requires Python 3.10+ and Java on PATH.

```python
from src.repairer import repair_epub

result = repair_epub("book.epub", "book-repaired.epub")
print(result)
```

Flow: validate original → diagnose PKG-006 using ZIP entry order → rebuild
into a temporary archive → revalidate → publish a separate output.
The original and any existing output are never overwritten. ZIP entries are
copied directly without extracting paths to disk. Output publication uses an
exclusive hard link on the destination filesystem; unsupported filesystems
return `output_write_failed`.

Only `mimetype_missing` and `mimetype_not_first` are supported. The rebuilt
archive starts with exactly `b"application/epub+zip"`, uncompressed; remaining
entries use deflate. Existing mimetype entries are not duplicated. Incorrect
existing mimetype content, duplicate ZIP entries, signatures and encryption
are refused. Other EPUBCheck error IDs are not repaired.

`original_valid` and `final_valid` are EPUBCheck results (`None` when unknown).
`detected` records diagnoses. `repaired` is populated only when revalidation
passes and output publication succeeds. `remaining_messages` retains the
latest validation messages. `output_file` is set only after publication.
`reason` and optional `detail` explain refusals/failures. A rebuilt EPUB with
remaining validation errors is published for inspection with
`reason="revalidation_failed"`, `final_valid=False`, and `repaired=[]`.
Already valid files and unsupported repairs produce no output.

Low-level `validate_epub` raises `FileNotFoundError` for a missing input and
`EPUBCheckError` for execution/report failures. Validation exit code 1 is a
normal invalid result. `diagnose_epub` inspects ZIPs only for PKG-006 and raises
standard ZIP/I/O exceptions for unreadable archives; orchestration converts
these failures into structured results.

## Tests

```sh
.venv/bin/python -m pip install 'pytest>=8,<10'
.venv/bin/python -m pytest -q
```

Tests generate small EPUB fixtures in temporary directories, including real
EPUBCheck validation and repair/revalidation of both PKG-006 causes. They
require the bundled JAR and Java; no Downloads files or network fixtures.
