Fixtures from EPUBCheck 5.4.0 test resources:

- epub2-missing-container.epub: epub2/files/epub/ocf-metainf-container-file-missing-fatal
- epub3-missing-container.epub: epub3/04-ocf/files/ocf-container-file-missing-fatal
- not-ocf.epub: epub3/04-ocf/files/ocf-container-not-ocf-error.epub

The first two expanded fixtures are packaged with their original file contents,
stored entries, mimetype first, and fixed ZIP timestamps (2020-01-01).
The third fixture is copied unchanged. Tests use only these repository files.
Source: https://github.com/w3c/epubcheck/tree/v5.4.0/src/test/resources

Discovery intentionally considers only .opf filenames (case insensitive).
XML parsing and the exact OPF package root decide acceptance. This does not
claim to discover package documents with arbitrary extensions. Inspection
refuses more than 64 candidates, over 32 MiB aggregate candidate data, or any
candidate over 8 MiB. Unsafe or unreadable candidates prevent a uniqueness claim;
malformed XML and well-formed non-package XML are not package documents.
