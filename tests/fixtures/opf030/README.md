# Reproducible OPF-030 fixtures

All fixtures are self-authored. Rebuild using only the standard library:

```sh
python3 tests/fixtures/opf030/build_fixtures.py
```

The builder reuses source constants from the neighboring RSC-008 and NCX-001
fixture generators. ZIP order, stored compression, timestamps and comments are
deterministic. The reproduction test compares every generated EPUB byte-for-byte.

Real bundled EPUBCheck 5.4.0 behavior:

| Fixture | Initial result | Repair behavior |
| --- | --- | --- |
| unresolved | OPF-030 + exact identifier-reference RSC-005, 0 fatal / 2 errors | Existing `absent` reference becomes `id`; final valid, 0 fatal/error |
| ncx-matching | OPF-030, 0 fatal / 1 error | Existing reference becomes `book-id`; NCX unchanged; final valid |
| ncx-mismatching | OPF-030 only | Revalidation reveals NCX-001; no output and no second repair pass |
| missing-reference | OPF-030 + OPF-048 + RSC-005 | Refuse; never create the missing attribute |
| empty-reference | OPF-030 + OPF-048 + two RSC-005 messages | Refuse; never populate an empty reference |
| already-resolved | Valid | No mutation |
| unrelated-error | OPF-030 plus package/chapter RSC-005 messages | Unrelated chapter error survives revalidation; no output |
| invalid-uuid | OPF-030 + reference RSC-005; no initial UUID warning | Revalidation reveals OPF-085 warning; identifier proof fails and no output is published |

Additional fixture variants exercise multiple identifiers, UUID plus ISBN,
identifier without ID, invalid ID, empty identifier and edge whitespace. Their
proof/refusal behavior is tested with controlled validator evidence; no claim
that all these negatives independently produce only OPF-030 is made.

Important: missing/empty unique-identifier DOES also produce OPF-030 in this
checker, contrary to assuming it always has a different error code. Presence of
OPF-030 alone is therefore insufficient. The safe subset requires the existing,
nonempty, unresolved reference and exactly one safe existing identifier.

The EPUB3 companion RSC-005 is specifically the schema consequence of an
unresolved package unique-identifier reference. Recognizing it does not repair
generic RSC-005 problems or suppress any messages; final real EPUBCheck decides
EPUB validity. The OPF-030 proof additionally refuses the invalid UUID warning
even though the general warnings policy reports valid=True; that truthful value
is retained in `final_valid`, with `repaired=[]`, no output and a precise reason.
