# EPUB Doctor

Small Python engine for deterministic repairs. Bundled EPUBCheck 5.4.0 is the
validation authority; Python diagnoses its reports rather than validating EPUB
content independently. Requires Python 3.10+ and Java on PATH.

```python
from src.repairer import repair_epub

result = repair_epub("book.epub", "book-repaired.epub")
print(result)
```

Flow: validate original → diagnose PKG-006/PKG-007 using ZIP entries → rebuild
into a temporary archive → revalidate → publish a separate output.
The original and any existing output are never overwritten. ZIP entries are
copied directly without extracting paths to disk. After successful revalidation,
output is created exclusively (`xb`) and copied from the validated archive.
This prevents overwrites without requiring hard links. The copy is not atomically
visible; a failed copy is removed and returns `output_write_failed`.

Only `mimetype_missing`, `mimetype_not_first` (PKG-006), and
`mimetype_incorrect_content` (PKG-007) are supported. Content diagnosis requires
an EPUBCheck PKG-007 message and exactly one readable, unencrypted root mimetype
entry whose bytes differ from the required value. The same rebuild handles
these diagnoses, including combined PKG-006/PKG-007 errors. The rebuilt
archive starts with exactly `b"application/epub+zip"`, uncompressed; remaining
entries retain their original compression methods where supported by Python's
zipfile implementation. Unsupported methods are refused rather than converted.
Existing mimetype entries are not duplicated. Incorrect content without a
PKG-007 message, duplicate ZIP entries, signatures and encryption are refused.
Missing root entries are not diagnosed as PKG-007; they require PKG-006.
Other EPUBCheck error IDs are not repaired.

`original_valid` and `final_valid` are EPUBCheck results (`None` when unknown).
`detected` records diagnoses. `repaired` is populated only when revalidation
passes and output publication succeeds. `remaining_messages` retains the
latest validation messages. `output_file` is set only after publication.
`reason` and optional `detail` explain refusals/failures. A rebuilt EPUB with
remaining validation errors is deleted, with `reason="revalidation_failed"`,
`final_valid=False`, `output_file=None`, and `repaired=[]`; validation messages
are preserved.
Already valid files and unsupported repairs produce no output.

Low-level `validate_epub` raises `FileNotFoundError` for a missing input and
`EPUBCheckError` for execution/report failures. Validation exit code 1 is a
normal invalid result. `diagnose_epub` inspects ZIPs only for PKG-006/PKG-007 and raises
standard ZIP/I/O exceptions for unreadable archives; orchestration converts
these failures into structured results.

## Tests

```sh
.venv/bin/python -m pip install 'pytest>=8,<10'
.venv/bin/python -m pytest -q
```

Tests generate small EPUB fixtures in temporary directories, including real
EPUBCheck validation and repair/revalidation of both PKG-006 causes and PKG-007. They
require the bundled JAR and Java; no Downloads files or network fixtures.
