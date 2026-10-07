# NCX-001 fixtures

Eleven small, self-authored EPUB2 archives generated reproducibly by
`build_fixtures.py`; no external downloads, checker customization or severity
overrides. Run from the repository root:

```sh
.venv/bin/python tests/fixtures/ncx001/build_fixtures.py
.venv/bin/python -m pytest -q tests/test_ncx001.py
```

| Fixture | Purpose |
| --- | --- |
| identifier-mismatch.epub | One canonical OPF identifier and wrong NCX dtb:uid; exactly one real NCX-001 ERROR. |
| multiple-identifiers.epub | Another identifier precedes the selected identifier; only package/@unique-identifier selects the canonical value. |
| already-matching.epub | Valid EPUB; no NCX-001 and no modification. |
| missing-unique-identifier.epub | No unique-identifier attribute. |
| unresolved-identifier.epub | Reference points to no dc:identifier. |
| empty-identifier.epub | Selected identifier has no text. |
| missing-ncx.epub | Manifest/spine references an absent NCX. |
| multiple-ncx.epub | Two NCX manifest resources; deliberately unsupported even with a toc reference. |
| missing-uid.epub | No dtb:uid; bundled EPUBCheck accepts this shape, so no UID is invented. |
| duplicate-uid.epub | Two head/meta dtb:uid targets. |
| missing-content.epub | UID meta exists without content attribute. |

Both positive fixtures repair to `urn:example:canonical` and independently pass
bundled EPUBCheck 5.4.0 with zero fatal/error/warning. All negative inputs remain
byte-for-byte unchanged. Invalid structures need not produce NCX-001: real
checker tests assert no repair, while explicitly injected NCX-001 reports test
the structural refusal reasons independently. The missing-UID fixture is valid
under the real checker; it is never "fixed" by constructing a new element.

Additional generated test archives cover EPUB3 compatibility, unsafe/ambiguous
OPF structures, ZIP extra fields, revalidation and publication failures. Byte
mutation tests cover namespaces, BOM, quotes, entities, Unicode identifiers and
unchanged comments/text. Original fixture bytes and unrelated members/ZIP
metadata are checked after repair.
