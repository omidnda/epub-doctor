# Self-authored RSC-008 fixtures

Rebuild byte-for-byte with the standard library:

```sh
python3 tests/fixtures/rsc008/build_fixtures.py
```

All ZIP entries use fixed timestamps, stored compression and preserved comments.
The base64 source assets are self-authored 1x1 PNG/JPEG/GIF images, initially
encoded with Pillow 12.1.1; neither the generator nor tests require Pillow.
No fixture or asset was downloaded.

Bundled EPUBCheck 5.4.0 results:

| Fixture | Initial errors | Expected behavior |
| --- | --- | --- |
| png.epub | One RSC-008 | Manifest-only repair; final valid |
| jpeg.epub | One RSC-008 | Manifest-only repair; final valid |
| multiple.epub | Three RSC-008 (PNG/JPEG/GIF) | Atomic repair; final valid |
| svg.epub | One RSC-008 | Static SVG declaration; final valid |
| css.epub | One RSC-008 | CSS declaration; final valid |
| xhtml.epub | One RSC-008 | Embedded iframe document declaration; final valid |
| linked-xhtml.epub | One RSC-008 | Refuse: would also require a spine change |
| scripted-xhtml.epub | One RSC-008 | Refuse: new item would require scripted properties |
| unrelated-error.epub | RSC-008 and RSC-005 | Revalidation fails; no output |
| already-declared.epub | None | Already valid; no new item |
| missing.epub | RSC-007 | Unsupported; no RSC-008 declaration repair |
| unknown.epub | RSC-008 | Refuse: unsupported type |

For RSC-008, `message` identifies the undeclared archive member; `locations[].path`
identifies the referring document. Tests independently confirm that the referrer
belongs to the selected publication and contains the literal reference.

The positive fixtures do not emit OPF-003 with this bundled checker. OPF-003
companion coverage uses its exact bundled English message, separate synthetic
evidence, and the same mandatory final validation gate; it is not presented as
a naturally occurring real-checker companion fixture. Font header and literal
CSS URL handling have focused unit/proof coverage, not real-font EPUB fixtures.
