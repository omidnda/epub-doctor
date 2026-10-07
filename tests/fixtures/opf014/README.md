# Official OPF-014 fixture copies

Source: [EPUBCheck 5.4.0 package-document test resources](https://github.com/w3c/epubcheck/tree/v5.4.0/src/test/resources/epub3/05-package-document/files).

Each EPUB is built from the correspondingly named official expanded fixture,
with all original file bytes unchanged. Packaging is deterministic: first root
`mimetype`, stored entries, sorted remaining file paths, timestamp 2020-01-01,
Unix creator/0600 attributes, and no ZIP extra fields. The ZIP packaging fits the
existing conservative metadata policy. Tests require no downloads or remote
fixture access; remote references are original fixture content for EPUBCheck,
not resources fetched or analyzed by EPUB Doctor.

- `package-manifest-prop-scripted-undeclared-javascript-error.epub`
- `package-manifest-prop-scripted-undeclared-form-error.epub`
- `package-manifest-prop-svg-undeclared-error.epub` (two report locations)
- `package-manifest-prop-svg-undeclared-partial-error.epub` (one missing token)
- `package-manifest-prop-remote-resource-undeclared-error.epub`
- `package-remote-audio-missing-property-error.epub`
- `package-remote-font-in-css-missing-property-error.epub`
- `package-remote-font-in-inline-css-missing-property-error.epub`
- `package-remote-font-in-svg-missing-property-error.epub`
- `package-remote-audio-in-overlays-missing-property-error.epub`

Bundled EPUBCheck 5.4.0 reports one OPF-014 error and no other errors/warnings
for each input. Successful property repairs are independently revalidated by
the integration tests; final reports must have zero fatals and errors. Nine
fixtures also have zero warnings. The inline-CSS fixture produces OPF-018 after
adding `remote-resources` under EPUBCheck 5.4.0. Tests explicitly retain this
warning in `remaining_messages`; the existing engine allows valid outputs with
warnings. The independent JavaScript E2E fixture finishes without warnings.

`refused/package-manifest-prop-switch-not-declared-error.epub` is packaged in the
same way from its official fixture. It reports OPF-014 for `switch` together
with RSC-017 and is deliberately never repaired.
