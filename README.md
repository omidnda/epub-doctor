# EPUB Doctor

Small Python engine for deterministic repairs. Bundled EPUBCheck 5.4.0 is the
validation authority; Python diagnoses its reports rather than validating EPUB
content independently. Requires Python 3.10+ and Java on PATH.

The ChatGPT adapter accepts uploaded-file references over Streamable HTTP.
With an explicit public HTTPS origin, successful validated repairs return an
external temporary download link, available for 15 minutes. This is not a native
ChatGPT attachment. See [delivery, security, retention and exact live-test commands](docs/temporary-epub-delivery.md).
Authentication/privacy/plugin-review decisions remain before public production.
See the [public privacy-policy draft](PRIVACY.md) and
[publication notes](docs/privacy-publication.md) for verified file/telemetry
handling and deployment details still to complete.

An isolated [external download-link investigation and live PoC](docs/external-download-investigation.md)
proved clickable-link/download behavior in the user's live ChatGPT test. It
remains a separate development utility serving fixed public text; production
delivery does not depend on the PoC.

```python
from src.service import inspect_epub, repair_epub_file

inspection = inspect_epub("book.epub")
result = repair_epub_file("book.epub", "book-repaired.epub")
print(result)
```

## Internal service API

`inspect_epub(path: str) -> dict` runs the existing validator and conservative
diagnoser without modifying the input or creating an output EPUB. Its exact
top-level schema is:

```text
{
    "valid": bool,
    "repairable": bool,
    "safe_repairs": list[str],
    "fatal_count": int,
    "error_count": int,
    "warning_count": int,
    "messages": list[dict],
    "publication": dict
}
```

`safe_repairs` contains the existing diagnosis names; `repairable` is exactly
`len(safe_repairs) > 0`. It describes confirmed supported diagnoses, not a
guarantee of successful repair: the repair pipeline still applies all safety
refusals and requires a valid recheck. Counts, messages and publication metadata
come from EPUBCheck unchanged; nested report fields follow EPUBCheck 5.4.0.
Validation uses a temporary JSON report that is cleaned up, never an output EPUB.

Inspection returns no partial result on failure. Existing exceptions propagate:
`FileNotFoundError`/other `OSError` for input access, `EPUBCheckError` for missing
Java/JAR or execution/report failures, and existing ZIP/decompression exceptions
or `UnsafeZipError` for diagnosis failures. A report that an EPUB is invalid
is normal inspection data, rather than an execution failure. Unsupported or
ambiguous cases that the diagnoser already declines yield an empty diagnosis list.

`repair_epub_file(input_path: str, output_path: str) -> dict` delegates directly
to `repair_epub`, preserving all result fields, reason codes, optional `detail`,
and safety checks. Expected operational failures remain structured repair
results as described below. The service adds no exception catches; unexpected
programming errors propagate from both functions.

The CLI calls this service and keeps its existing arguments, printed result,
and exit behavior:

```sh
python3 -m src.main input.epub output.epub
```

Flow: validate original → diagnose supported issues → inspect ZIP safety → rebuild
into a temporary archive → revalidate → publish a separate output.
The original and any existing output are never overwritten. ZIP entries are
copied directly without extracting paths to disk. After successful revalidation,
output is created exclusively (`xb`) and copied from the validated archive.
This prevents overwrites without requiring hard links. The copy is not atomically
visible; a failed copy is removed and returns `output_write_failed`.

Supported mimetype diagnoses are `mimetype_missing`, `mimetype_not_first` (PKG-006),
`mimetype_incorrect_content` (PKG-007), and `mimetype_has_extra_field` (PKG-005)
for the corresponding PKG reports. Content diagnosis requires
an EPUBCheck PKG-007 message and exactly one readable, unencrypted root mimetype
entry whose bytes differ from the required value. The same rebuild handles
these diagnoses, including combined mimetype errors. The rebuilt
archive starts with exactly `b"application/epub+zip"`, uncompressed and with no
local or central mimetype extra fields; remaining
entries retain their original compression methods where supported by Python's
zipfile implementation. Unsupported methods are refused rather than converted.
Existing mimetype entries are not duplicated. Incorrect content without a
PKG-007 message, duplicate ZIP entries, signatures and encryption are refused.
Missing root entries are not diagnosed as PKG-007; they require PKG-006.
Other EPUBCheck error IDs are not repaired.

PKG-005 diagnosis requires an EPUBCheck PKG-005 message, exactly one root
`mimetype`, and a confirmed nonzero extra-field length in its physical local
header. `ZipInfo.extra` alone is not sufficient. The bounded inspection checks
local signatures, names, offsets, payload boundaries, flags, sizes, CRC metadata,
timestamps and versions against the central directory. Conflicting/truncated
records and ambiguous layouts are refused with `invalid_zip_structure`.

Metadata policy is deliberately conservative for all supported repairs:
non-mimetype entries with **any local or central extra fields** are refused with
`unsafe_metadata_preservation`, even when those fields match. This avoids losing
local-only metadata or carrying unknown extra fields with offset-dependent data.
The official PKG-005 fixture has extra fields on other entries, so it is diagnosed
but intentionally refused. Generated fixtures exercise the safely repairable subset.
No generic ZIP rewriting mechanism is used.

For accepted archives, non-mimetype contents, compression methods, DOS timestamps,
entry comments, attributes, creator metadata and the archive comment are retained.
Compressed streams, sizes, CRCs and transport flags are regenerated by zipfile;
the result is not a byte-for-byte copy of the original ZIP structure. Only mimetype's
extra metadata is deliberately discarded. ZIP64 is refused with `unsupported_zip64`.
Data descriptors and unsupported flags are refused with `unsupported_zip_structure`;
split/prefixed archives, reordered headers, gaps and overlapping records are refused.
Unflagged non-ASCII names are not decoded by guessing an encoding.

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
normal invalid result. `diagnose_epub` inspects ZIPs only for supported messages and raises
standard ZIP/I/O exceptions or `UnsafeZipError` for unsafe structures; orchestration converts
these failures into structured results.

## Tests

```sh
.venv/bin/python -m pip install 'pytest>=8,<10'
.venv/bin/python -m pytest -q
```

Tests generate small EPUB fixtures in temporary directories, including real
EPUBCheck validation and repair/revalidation of PKG-005, both PKG-006 causes, and PKG-007. They
require the bundled JAR and Java; no Downloads files or network fixtures.

## Missing-container diagnosis and repair (RSC-002)

`container_xml_missing` diagnoses a missing container in a conservative subset.
It requires an actual RSC-002 report, no PKG-005/006/007 report,
an absent `META-INF/container.xml`, unique ZIP names, and the existing trustworthy
local/central ZIP header inspection. Signed/encrypted publications or entries
are refused. Exactly one first, stored, unencrypted `mimetype` entry must contain
`application/epub+zip` exactly and have no local or central extra fields.

Package discovery deliberately considers only filenames ending in `.opf`
(case insensitive). Each candidate must have a safe, literal ZIP path, and its
XML root must be exactly `{http://www.idpf.org/2007/opf}package`. Version strings
are not used as a gate: EPUB2 and EPUB3 package roots are both recognized.
Zero or multiple package documents are refused. Malformed XML and non-package
roots do not count; DTDs, unsafe paths, unreadable candidates, and oversized
candidates prevent a positive diagnosis, including when another package exists.

The parser limit is 8 MiB per XML; discovery additionally refuses more than 64
candidates or over 32 MiB total declared candidate bytes. Unrelated payloads are
not read or parsed. This intentionally does not discover packages with arbitrary
extensions or claim that other EPUB errors are absent. Existing ZIP64,
descriptor, unsupported flag/compression, and inconsistent-header refusals apply.
Repository-owned [official fixture copies](tests/fixtures/rsc002/README.md)
exercise EPUBCheck 5.4.0 with missing-container EPUB2/EPUB3 and a DOCX archive.

Diagnosis and repair share `_find_unique_package_path`, which returns the exact
proven ZIP entry name or `None`; the public diagnosis API remains `list[str]`.
After diagnosis, repair rechecks the same evidence against the open source ZIP.
It builds UTF-8 container XML using ElementTree attribute escaping (no raw path
interpolation), then reparses and checks the OCF namespace, version, unique
rootfiles/rootfile, exact full-path, and standard package media type.

RSC-002 repair adds only `META-INF/container.xml`, immediately after the existing
mimetype entry. Its ZIP timestamp is fixed to 2020-01-01, its compression is
ZIP_STORED, and its creator system/permissions are fixed to Unix/0600. Existing
entries retain their order, data, compression methods, and the metadata covered
by the current policy; mimetype is copied without regeneration. Non-mimetype
extra fields and any additional metadata normalization remain refusals. RSC-002
is not combined with mimetype repairs. Output is published exclusively only
after successful EPUBCheck revalidation; failures retain remaining messages and
publish no output. Compressed streams and transport metadata may be regenerated
as described by the existing ZIP rebuild policy.

## Local MCP server

The adapter uses the official Python MCP SDK **2.3.0** (`mcp>=2.3,<3`),
`mcp.server.MCPServer`, `@mcp.tool()`, and `run(transport="stdio")`.
The SDK was checked against its installed API and
[official documentation](https://py.sdk.modelcontextprotocol.io/).
Only these two tools are exposed:

| Tool | Required input | Structured output |
| --- | --- | --- |
| `inspect_epub` | `path: str` | The exact eight-field inspection schema above |
| `repair_epub` | `input_path: str`, `output_path: str` | `original_valid: bool \| None`, `detected: list[str]`, `repaired: list[str]`, `final_valid: bool \| None`, `output_file: str \| None`, `remaining_messages: list[dict]`, `reason: str \| None`; optional `detail: str` |

Results are returned unchanged from the service in MCP `structured_content`,
with equivalent JSON text in `content`. The SDK publishes string input schemas
and object output schemas; the precise result fields are the service contract.
Inspection's `repairable` still means only that `safe_repairs` is non-empty.
A successful validated repair requires the service to publish an output; no
adapter logic decides whether repair succeeded.

**Local paths only:** files must be accessible to the MCP process under its
normal filesystem permissions. Absolute paths are recommended; relative paths
resolve against the server's working directory, not the client's. Arguments
are passed verbatim to the service. URL-like strings are treated as filesystem
paths, never downloaded or decoded. There are no uploads, base64 transfer,
cloud storage, file endpoints, or network listeners. All existing no-overwrite
and conservative EPUB/ZIP safety checks remain in the engine.

Expected inspection exceptions become SDK `ToolError` responses (`is_error=True`,
no structured result) with distinguishable labels: `input_not_found`,
`input_read_failed`, `epubcheck_failure`, `invalid_zip`, `unsupported_zip`,
`unsupported_zip64`, or the existing `UnsafeZipError.reason`. An invalid EPUB
that EPUBCheck can report remains normal inspection data (`valid=False`), not
an execution error. Diagnosis failures that the existing diagnoser declines
still return its empty diagnosis list.

Repair refusals and operational failures already represented by the service
remain normal structured MCP results (`is_error=False`), including their
`reason` and optional `detail`; callers must inspect those fields. For example,
a missing repair input has `reason="input_read_failed"`, EPUBCheck execution
failure has `reason="epubcheck_failure"`, corrupt ZIP repair has
`reason="invalid_zip"`, and an existing destination has `reason="output_exists"`.
The adapter catches no broad `Exception` or generic `RuntimeError`. Unexpected
errors propagate to the SDK, which logs a traceback and returns a sanitized
failed tool call rather than disguising them as domain refusals.

Run from the repository root (Python 3.10+ and Java on PATH):

```sh
.venv/bin/python -m pip install 'mcp>=2.3,<3'
.venv/bin/python -m src.mcp_server
```

The process waits for MCP messages on stdin; stdout carries protocol data.
A local MCP host can launch it using this configuration (adjust absolute paths
for your checkout; `cwd` is supported by the SDK client and many local hosts):

```json
{
  "mcpServers": {
    "epub-doctor": {
      "command": "/home/omid/Desktop/projects/epub-doctor/.venv/bin/python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/home/omid/Desktop/projects/epub-doctor"
    }
  }
}
```

For hosts without a `cwd` setting, set `env.PYTHONPATH` to the repository root
and use absolute EPUB paths.

### Manual stdio smoke test

Run this from the repository root after installing the SDK. It starts the server
as a local subprocess through the official SDK client, lists both tools,
inspects the known RSC-002 fixture, repairs to a temporary output, re-inspects,
and confirms an existing-output refusal. It leaves the fixture unchanged and
cleans up its output; no deployment or Inspector installation is needed.

```sh
.venv/bin/python - <<'PY'
import asyncio
import sys
import tempfile
from pathlib import Path
from mcp import Client, StdioServerParameters

async def smoke():
    root = Path.cwd()
    source = root / "tests/fixtures/rsc002/epub3-missing-container.epub"
    before = source.read_bytes()
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "src.mcp_server"], cwd=str(root)
    )
    with tempfile.TemporaryDirectory() as directory:
        output = str(Path(directory) / "repaired.epub")
        async with Client(params, read_timeout_seconds=60) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert names == {"inspect_epub", "repair_epub"}
            inspected = await client.call_tool("inspect_epub", {"path": str(source)})
            assert not inspected.is_error
            assert inspected.structured_content["safe_repairs"] == ["container_xml_missing"]
            assert inspected.structured_content["repairable"] is True
            assert not Path(output).exists()
            repaired = await client.call_tool("repair_epub", {
                "input_path": str(source), "output_path": output
            })
            assert not repaired.is_error
            assert repaired.structured_content["final_valid"] is True
            assert repaired.structured_content["output_file"] == output
            assert repaired.structured_content["repaired"] == ["container_xml_missing"]
            checked = await client.call_tool("inspect_epub", {"path": output})
            assert not checked.is_error and checked.structured_content["valid"] is True
            refused = await client.call_tool("repair_epub", {
                "input_path": str(source), "output_path": output
            })
            assert not refused.is_error
            assert refused.structured_content["reason"] == "output_exists"
            assert source.read_bytes() == before
            print("MCP stdio smoke test passed")

asyncio.run(smoke())
PY
```

Focused MCP tests: `.venv/bin/python -m pytest -q tests/test_mcp_server.py`.
Full regression suite: `.venv/bin/python -m pytest -q`.

## Required manifest properties (OPF-014)

`manifest_missing_required_property` repairs only the exact English EPUBCheck
5.4.0 message `The property "<property>" should be declared in the OPF file.`
for `scripted`, `svg`, or `remote-resources`. `switch`, future properties,
malformed messages, missing/unsafe locations, and reports with omitted
`additionalLocations` are refused. EPUBCheck remains the sole content detector:
EPUB Doctor does not analyze JavaScript, forms, SVG, CSS, fonts or audio.

Diagnosis and repair share `_manifest_property_replacement`, including the
complete mutation proof. Package resolution reuses the existing single-rootfile
OCF logic from RSC-003 and the bounded XML reader. This subset requires an
existing unique standard-media-type rootfile, an EPUB3 `version="3.0"` OPF,
UTF-8 XML (optional BOM), no DTD or `xml:base`, exactly one direct manifest,
and unique nonempty manifest IDs. XML is bounded at 8 MiB; Expat additionally
refuses documents reaching 100,000 recorded events before a start element.

Report locations must be exact safe archive resource names. Manifest `href`s
are resolved relative to the package directory using URI components and a path
segment stack: `.` is removed and `..` may ascend within the archive but never
above its root. Zero/multiple matches, missing resources, directory resources,
and already-declared required tokens refuse the whole OPF-014 set. Unrelated
HTTP(S) manifest entries remain remote and cannot match local locations.
Percent-encoded hrefs, absolute local paths, query/fragment suffixes, empty path
segments, control characters, backslashes and other schemes are outside this
conservative subset; an unsupported href anywhere prevents a mapping proof.
No URI decoding, content detection or additional package discovery is performed.

Every reported message and every location must pass before any mutation is
returned. Repeated evidence is coalesced. Multiple missing tokens for the same
item are appended once in deterministic lexical order, preserving existing
property tokens and their original spelling/whitespace. Expat locates each
actual manifest-item start tag, and a quoted-attribute scanner identifies its
byte offsets. Mutation inserts only ASCII property tokens into that attribute
value or adds a `properties` attribute to that exact tag. It does not serialize
the document, change namespaces, or rewrite comments, quotes or unrelated bytes.
A second parse verifies the entire semantic event stream against the original
with only the proven property-attribute additions, and verifies token uniqueness.

OPF-014 diagnosis additionally requires a canonical mimetype and all existing
ZIP layout/metadata checks, including no non-mimetype extra fields. This phase
does not combine OPF-014 with damaged mimetype or missing/incorrect containers.
The existing rebuild/revalidation/exclusive-output pipeline publishes output
only when EPUBCheck reports it valid. Service/MCP schemas and tools are unchanged.
As before, validity permits warnings, and `remaining_messages` preserves them.

Unsupported/unsafe evidence normally yields no OPF-014 diagnosis and
`reason="no_supported_repair"` if no other supported diagnosis exists. Existing
early refusals such as `signatures_or_encryption`, `duplicate_zip_entries`,
`output_exists` and `output_is_input` remain unchanged. If previously confirmed
OPF evidence cannot be reconfirmed, or XML mutation fails proof, the structured
repair refusal uses `unsafe_manifest_property_repair` with `detail`.
Failed revalidation returns `revalidation_failed` and publishes no output.

[Official fixture copies](tests/fixtures/opf014/README.md) cover all ten requested
cases and the excluded `switch` case. Nine supported fixtures revalidate with
zero errors/warnings. With bundled EPUBCheck 5.4.0, the inline-CSS remote-font
fixture becomes valid but emits OPF-018 after its reported property is added;
tests explicitly check and retain that warning. No checker behavior or warning
policy is changed. The JavaScript fixture provides a clean independent E2E.

```sh
.venv/bin/python -m pytest -q tests/test_opf014.py
```

To reproduce independent before/after EPUBCheck verification for the clean
JavaScript fixture, run from the repository root (the first checker invocation
normally exits 1 because OPF-014 is present):

```sh
opf014_evidence_dir=$(mktemp -d)
java -jar vendor/epubcheck-5.4.0/epubcheck.jar tests/fixtures/opf014/package-manifest-prop-scripted-undeclared-javascript-error.epub --json "$opf014_evidence_dir/before.json"
.venv/bin/python -m src.main tests/fixtures/opf014/package-manifest-prop-scripted-undeclared-javascript-error.epub "$opf014_evidence_dir/repaired.epub"
java -jar vendor/epubcheck-5.4.0/epubcheck.jar "$opf014_evidence_dir/repaired.epub" --json "$opf014_evidence_dir/after.json"
```

The first report has OPF-014 at `EPUB/content_001.xhtml`. The service result has
`detected`/`repaired=["manifest_missing_required_property"]`, `final_valid=True`,
and no remaining messages. The independent output check has zero fatals,
errors and warnings. Only the content item's `properties="scripted"` attribute
is added in `EPUB/package.opf`; the existing navigation property is unchanged.

## NCX identifier mismatch (NCX-001)

The seventh safe family is `ncx_identifier_mismatch`. [EPUBCheck's message reference](https://w3c.github.io/epubcheck/docs/messages/)
defines NCX-001 as a mismatch between the NCX and OPF identifiers. The bundled
5.4.0 checker emits an ERROR for the repository's real positive fixtures.

Diagnosis requires actual NCX-001 evidence plus independent proof:

- One existing OCF rootfile selects an OPF 2.0/3.0 package, without xml:base.
- `package/@unique-identifier` resolves to exactly one direct metadata
  `dc:identifier`, with a globally unique matching ID. Multiple distinct
  identifiers are supported; only the explicitly referenced one is used.
- The canonical identifier is nonempty plain XML text, without leading/trailing
  whitespace, controls or child elements. No identifier-format guess, UUID
  generation, ISBN inference or normalization is performed. Known validator
  evidence of an invalid/unresolved identifier (OPF-030/048/085) refuses repair.
- Exactly one NCX manifest item is selected explicitly by `spine/@toc`.
  Its safe local href resolves to one archive member without aliases, and every
  NCX-001 report location matches that member. URI mapping reuses the existing
  conservative manifest resolver; no percent decoding or remote target fetch.
- Exactly one correctly namespaced direct `ncx/head/meta` has `name="dtb:uid"`
  and an existing nonempty literal `content` value that differs from the OPF
  identifier. Missing elements/attributes are never created.

Expat identifies the exact attribute byte span; mutation replaces only that
value with the safely XML-escaped canonical identifier. A second parse verifies
the full semantic event stream with only that attribute changed. NCX whitespace,
quotes, comments, namespaces, unrelated XML and other ZIP members remain intact.
The existing rebuild, ZIP metadata preservation, real EPUBCheck revalidation
and exclusive-output publication pipeline is reused unchanged.

Refusals preserve `repaired=[]`, `output_file=None` and the original input.
Reasons include `ncx_unique_identifier_missing`, `ncx_identifier_ambiguous`,
`ncx_identifier_invalid`, `ncx_package_ambiguous`, `ncx_package_unsupported`,
`ncx_target_ambiguous`, `ncx_target_unreadable`, `ncx_evidence_ambiguous`,
`unsafe_ncx_archive`, `unsafe_ncx_identifier_proof` and
`unsafe_ncx_identifier_mutation`; `detail` identifies the failed proof.
Inspection exposes the family only after complete proof. If NCX-001 was never
reported, existing `already_valid` / `no_supported_repair` behavior applies.
Failed final validation still returns `revalidation_failed` and no output.

Intentional limits: one rootfile/publication, one declared NCX, explicit
spine/@toc, canonical mimetype and existing preservable ZIP structure with no
extra fields. This subset does not combine NCX repair with damaged mimetype or
container repairs. XML reads are bounded to 8 MiB each; NCX mutation supports
UTF-8 (including BOM). Element starts are refused at 100,000 accumulated semantic
events or 128 levels of nesting. DTDs and custom entity declarations,
non-UTF-8 NCX, xml:base, ambiguous/aliased targets and non-ASCII reference IDs are
refused. Canonical identifier text itself may be Unicode; it is copied without
inventing a new value. Other errors are never repaired by this family and must
still pass final validation. Service/MCP contracts and temporary delivery are
unchanged; no new dependency or RSC-008 repair was added.

[Small reproducible fixtures](tests/fixtures/ncx001/README.md) include real
NCX-001 mismatch cases and conservative refusals:

```sh
.venv/bin/python -m pytest -q tests/test_ncx001.py
.venv/bin/python -m src.main tests/fixtures/ncx001/identifier-mismatch.epub repaired-ncx.epub
java -jar vendor/epubcheck-5.4.0/epubcheck.jar repaired-ncx.epub --json /tmp/ncx-after.json
```

The output pathname must not already exist. Positive EPUB2 fixtures and a
generated EPUB3 test independently validate with zero fatal/error after repair.

## ChatGPT file-input MCP adapter (development)

`src/chatgpt_mcp_server.py` is a separate adapter. The original
`python -m src.mcp_server` still exposes local path-based tools over stdio.
Core/service files, ZIP checks, repair families and validation semantics
are unchanged. No dependency was added: the installed official MCP SDK 2.3.0
provides `MCPServer`, tool metadata and Streamable HTTP; downloads use stdlib.

The new adapter exposes exactly `inspect_epub(file)` and `repair_epub(file)`.
Both input schemas contain one object named `file`:

```json
{
  "download_url": "https://public-download-host.example/signed-object",
  "file_id": "file-reference-id",
  "mime_type": "application/epub+zip",
  "file_name": "book.epub"
}
```

This is a schema example, not a working download URL. Only `download_url` and
`file_id` are required. Both optional fields are declared, as required by the
[official ChatGPT file input contract](https://developers.openai.com/plugins/reference).
Each tool publishes `_meta: {"openai/fileParams": ["file"]}`. `OpenAIFile` is
the exact documented schema expressed as a TypedDict; the installed SDK has
no built-in class with that name. Neither tool accepts local paths.

Downloads accept credential-free HTTPS URLs on port 443 only. Signed query
parameters are supported, but full URLs/tokens are not included in adapter
errors or logs. DNS is resolved once; every returned address must be public,
excluding private, loopback, link-local, multicast, reserved and IPv6 transition
addresses. The verified IP is pinned to the connection while the original
hostname is retained for Host, TLS certificate verification and SNI. Environment
proxy settings are not used. All redirects and HTTP content compression are
refused. Uploaded filenames and MIME types are untrusted hints; EPUBCheck
determines validity, and filenames never determine filesystem paths.

`MAX_INPUT_BYTES` is **20 MiB (20,971,520 bytes)**. Oversized Content-Length is
refused before reading, and streaming byte accounting enforces the same limit
when the header is missing or misleading. `DOWNLOAD_TIMEOUT_SECONDS=15` applies
to socket connection/read operations. `DOWNLOAD_BUDGET_SECONDS=60` is checked
between reads; it is not a hard deadline for OS DNS resolution or a blocking
operation. The 20 MiB cap concerns downloaded input bytes. Core diagnosis/repair
also enforce the uncompressed archive limits documented below.

Each call owns a private `TemporaryDirectory` with controlled `input.epub` and,
for repair only, `repaired.epub`. Context-manager cleanup removes partial input
and output on success, refusal and exceptions; cleanup failure is reported
rather than silently ignored. Inspection creates no output EPUB.

Inspection returns the exact eight service fields above, unchanged. Repair
returns this envelope:

```text
{
  "repair": {
    "original_valid": bool | None,
    "detected": list[str],
    "repaired": list[str],
    "final_valid": bool | None,
    "output_file": None,
    "remaining_messages": list[dict],
    "reason": str | None,
    "detail": str                 # optional, preserved when present
  },
  "file_output": {
    "status": "available" | "unavailable" | "failed",
    "delivered": bool,
    "detail": str,               # optional; disabled/refused/failed delivery
    "download_url": str,         # success only: configured HTTPS capability URL
    "expires_in_seconds": int,   # success only: 900
    "file_name": str             # success only: sanitized display name
  }
}
```

The service result itself is unchanged. Its internal temporary output path is
projected to `None` in the remote response because that file is deleted before
returning. All other repair fields, including optional `detail`, retain their
service values. `repaired`/`final_valid` describe validated repair; only
`file_output.delivered=True` indicates publication of a downloadable object.
Refusal or failed revalidation remains a normal structured tool result and never
publishes output. Publication failure retains repair semantics but returns
`status=failed`, `delivered=False`, without a URL. Without `--public-origin`,
delivery remains explicitly unavailable and workspace output is deleted.

**Native output limitation:** the inspected SDK and official documentation did
not establish a server API to register generated EPUB bytes with ChatGPT and
return a supported downloadable file reference. Generic MCP ResourceLinks do
not prove ChatGPT delivery support. No resource link, filesystem path, localhost
URL, data URL or base64 payload is advertised as a downloadable file. Configured
delivery instead returns an ordinary external HTTPS capability link to the
temporary validated copy; it does not require or claim native registration.

Expected malformed arguments become SDK tool errors. Transfer failures use
safe `FileDownloadError` messages translated to `ToolError`, including
`invalid_file_reference`, `invalid_download_url`, `unsafe_download_host`,
`file_too_large`, `download_timeout`, `download_redirect_refused`,
`download_encoding_refused`, `download_failed` and `temporary_workspace_failed`.
Inspection reuses the local adapter's existing service-error translation:
missing input, EPUBCheck execution/report failure and invalid/unsafe ZIP failures
remain distinct. Repair preserves the service's structured failures/refusals.
No broad exception handler hides programming errors; the official SDK handles
uncaught tool exceptions at its boundary.

Run from the repository root:

```sh
# Existing local path tools:
.venv/bin/python -m src.mcp_server
# File-reference tools, stdio:
.venv/bin/python -m src.chatgpt_mcp_server
# File-reference tools, development HTTP endpoint http://127.0.0.1:8000/mcp:
.venv/bin/python -m src.chatgpt_mcp_server --transport streamable-http --host 127.0.0.1 --port 8000
# Enable real temporary output delivery: use your actual public HTTPS hostname.
.venv/bin/python -m src.chatgpt_mcp_server --transport streamable-http \
  --public-origin https://YOUR-ACTUAL-HOST.trycloudflare.com
```

The HTTP entry point uses the SDK's `/mcp`, stateless Streamable HTTP and JSON
responses. DNS-rebinding protection remains enabled. For a development public
HTTPS endpoint, add its exact Host/Origin with `--allowed-host dev.example.org`
and `--allowed-origin https://dev.example.org` when using the older inspection-only
HTTP configuration. `--public-origin` automatically allowlists its exact Host /
Origin and enables downloads; links require HTTPS port 443. This server implements
no authentication and binds loopback by default. A Docker Compose deployment is described below. ChatGPT needs
[public HTTPS or the supported secure MCP tunnel](https://developers.openai.com/plugins/deploy/connect-chatgpt);
a localhost endpoint is only for local MCP clients. Official custom-server
[documentation permits development with no authentication](https://developers.openai.com/api/docs/guides/custom-mcp-server).

For a local HTTP initialization/discovery smoke test, leave the server running
in one terminal and run this in another (requires no EPUB download):

```sh
.venv/bin/python - <<'PY'
import asyncio
from mcp import Client

async def smoke():
    async with Client("http://127.0.0.1:8000/mcp") as client:
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == {"inspect_epub", "repair_epub"}
        assert all(tool.meta["openai/fileParams"] == ["file"] for tool in tools)
        print("HTTP initialization and tool discovery passed")

asyncio.run(smoke())
PY
```

For a reproducible fixture integration smoke test without external network or
ChatGPT, use the repository-owned OPF-014 JavaScript fixture:

```sh
.venv/bin/python -m pytest -q tests/test_chatgpt_mcp_server.py -k 'real_ or http_initialization or stdio_discovery'
.venv/bin/python -m pytest -q tests/test_chatgpt_files.py tests/test_chatgpt_mcp_server.py
.venv/bin/python -m pytest -q
```

The integration tests supply fixture bytes through a fake HTTPS response, invoke
the real service via the SDK, verify successful revalidation and the generated
OPF property, and assert input/output workspace cleanup. Unconfigured adapter
tests retain delivery=false; configured [delivery tests](tests/test_chatgpt_download_delivery.py)
download exact published bytes and independently validate them with EPUBCheck.
Additional SDK HTTP tests initialize `/mcp`, discover both tools, invoke file
inspection and check Host rejection. Unit downloads never contact real network.

## Missing referenced manifest declarations (RSC-008)

The eighth safe family is `manifest_missing_referenced_resource`. It adds only
manifest declarations for resources explicitly reported by bundled EPUBCheck
5.4.0, never unreported files discovered by scanning the ZIP. The checker message
names the target member; its location names the **referring document**. Both are
required, and the existing declared XHTML/SVG/CSS referrer must independently
contain a supported literal reference to that exact target.

This subset requires a canonical, unsigned, unencrypted OCF archive with unique
members, safe paths, no ZIP extra fields and one existing supported rootfile.
The EPUB3 OPF must be UTF-8, DTD-free, without xml:base, and have a unique nonempty
manifest. Existing manifest hrefs must resolve uniquely through the engine's
existing resolver. An already-declared target, ambiguous publication, missing
resource, unsupported URI, mismatched evidence or unprovable type refuses the
entire operation. No existing IDs or publication identifiers are changed.

The explicit media-type allowlist follows [EPUB core media types](https://www.w3.org/TR/epub-33/#sec-core-media-types):

| Extensions | Declared media type | Independent content check |
| --- | --- | --- |
| png | image/png | PNG signature, IHDR header and nonzero dimensions |
| jpg, jpeg | image/jpeg | JPEG start/end signatures |
| gif | image/gif | GIF87a/89a header, dimensions and trailer |
| svg | image/svg+xml | UTF-8 XML with SVG namespace/root |
| css | text/css | Nonempty UTF-8 text, no NUL or HTML/XML prefix |
| xhtml, html | application/xhtml+xml | UTF-8 XML with XHTML namespace/root |
| ttf | font/ttf | TrueType sfnt header and bounded table directory |
| otf | font/otf | OTTO header and bounded table directory |
| woff, woff2 | font/woff, font/woff2 | Matching signature, supported flavor and length/header |

These format checks prove the narrow declaration mapping, not complete resource
validity. Real final EPUBCheck is still mandatory. HTML syntax without the XHTML
XML namespace is unsupported. New script/event-handler/nav/embedded-SVG content
requiring properties is refused; simultaneous OPF-014 edits are refused rather
than combine two mutations of the same OPF. Linked XHTML is refused because
EPUBCheck additionally requires a spine item; embedded XHTML via iframe/object
can be declared without changing spine. No navigation, content, CSS, resource,
reading order, properties or metadata repair is performed by this family.

Targets are deduplicated and sorted by exact archive path. Internal NCName-safe
IDs `epub-doctor-resource-N` skip all existing unqualified/XML IDs deterministically.
Relative hrefs are generated from the OPF directory and reconfirmed with the
existing resolver. All new item bytes are inserted immediately before the
parser-proven manifest closing tag, reusing its namespace prefix. The complete
before/after XML event streams must differ only by the planned item events.
Existing OPF bytes, metadata, spine, comments, item declarations and unrelated
ZIP members/metadata remain intact. Any failed target refuses all additions.

Only exact OPF-003 messages identifying one of these fully proven targets, with
locations in the same target/package, are accepted as declaration companions.
Unrecognized/unrelated OPF-003 evidence refuses. Other errors are not suppressed:
the unchanged rebuild/revalidation/exclusive-publication pipeline publishes only
a final valid EPUB, with `repaired` empty on failure and no invalid output.
Service, MCP, temporary download delivery and result schemas are unchanged.

Intentional limits: EPUB3 only; canonical ASCII target/package/referrer paths;
no schemes, percent encoding, query, fragment evidence, xml:base, HTML base,
manifest/reference aliases, DTDs or non-UTF-8 XML. Referrers support explicit
XHTML resource attributes, SVG image hrefs and literal CSS url() tokens. CSS
escapes/imports and inline style references are outside this subset. Up to 128
complete RSC-008 reports, 8 MiB per inspected member and 32 MiB total proof bytes
are supported. Unknown type/magic and unsupported properties/structure refuse;
final errors prevent publication. Warnings retain the existing validator policy.
No network access or new dependency is used.

Precise failures use `rsc008_evidence_ambiguous`, `rsc008_resource_missing`,
`rsc008_resource_ambiguous`, `rsc008_resource_already_declared`,
`rsc008_media_type_unsupported`, `rsc008_package_ambiguous`,
`rsc008_remote_resource`, `rsc008_properties_unsupported`,
`rsc008_resource_requires_spine`, `rsc008_companion_unsupported`,
`unsafe_rsc008_archive` and `unsafe_rsc008_manifest_mutation`, with detail.
Existing archive-wide refusals such as `duplicate_zip_entries` and
`signatures_or_encryption` retain precedence in repair results.

[Reproducible fixtures and real-checker evidence](tests/fixtures/rsc008/README.md)
cover PNG, JPEG, multiple images, SVG, CSS and embedded XHTML. Linked XHTML and
scripted XHTML are conservative refusals. A real unrelated XML error exercises
failed revalidation. Font mappings have unit/header and CSS-referrer proof tests;
no real-font end-to-end claim is made. This checker did not emit OPF-003 alongside
the real positive fixtures; its companion tests are explicitly synthetic.

```sh
.venv/bin/python -m pytest -q tests/test_rsc008.py
python3 -m src.main tests/fixtures/rsc008/multiple.epub /tmp/rsc008-repaired.epub
java -jar vendor/epubcheck-5.4.0/epubcheck.jar /tmp/rsc008-repaired.epub
```

Use a new, absent output path; existing outputs are never overwritten.

## Unresolved package identifier reference (OPF-030)

The ninth safe family is `package_unique_identifier_unresolved`. [EPUBCheck's
message reference](https://w3c.github.io/epubcheck/docs/messages/) describes
OPF-030 as a unique-identifier that was not found. Real bundled 5.4.0 fixtures
confirm that an unresolved existing reference produces OPF-030; EPUB3 additionally
reports the exact schema RSC-005 about that reference not resolving to a
dc:identifier. Missing/empty references also produce OPF-030, along with OPF-048
and schema errors, and intentionally remain unsupported.

Proof requires one existing unambiguous package rootfile, an EPUB2/EPUB3 UTF-8
OPF, one direct metadata element and exactly one direct dc:identifier. Its
existing unqualified id must be a nonempty ASCII NCName and unique across all
unqualified/XML IDs in the package. Its content must be nonempty plain text,
without edge whitespace, controls, child elements, comments or processing
instructions. No identifier filtering or UUID/ISBN/scheme preference is used:
multiple identifiers always refuse, including partially invalid alternatives.

The existing package unique-identifier must itself be a nonempty ASCII NCName,
and its current value must name no existing package element. Every complete
OPF-030 report must have the exact current-reference message and locations in
that proven package. Absent/mismatched/incomplete evidence refuses. A reference
that already names an element (including a wrong-kind element) is outside this
absent-ID subset. Candidate values are neither generated nor normalized; the
[EPUB identifier requirement](https://www.w3.org/TR/epub-33/#sec-opf-dcidentifier)
is honored by reusing only the sole existing identifier.

Only the existing attribute value is replaced. Parser-proven byte offsets reuse
the engine's existing attribute scanner; quotes, whitespace, comments, namespaces,
metadata, manifest, spine, existing IDs and identifier content remain unchanged.
Full before/after XML event streams must differ only in this one root attribute.
DTD/entity declarations, xml:base, unsupported encoding, malformed/oversized XML,
depth above 128 and excessive parser events refuse. Reads retain the 8 MiB bound.
Archive proofs retain canonical mimetype, unique safe members, signature/encryption
refusals and local/central header/metadata checks; extra fields refuse.

The exact proven EPUB3 identifier-reference RSC-005 is the only recognized direct
schema companion; mismatched reference/package evidence refuses. Generic RSC-005
errors are not fixed, suppressed or reinterpreted, and unrelated errors prevent
publication when final EPUBCheck still reports them. OPF-048 and invalid UUID
metadata (OPF-085) cannot authorize identifier creation or normalization.

The pipeline plans from the original report and revalidates once; it is not an
iterative fixer. Concurrent OPF-014/RSC-008 edits of the same OPF are refused.
Existing NCX repair already refuses initial OPF-030 evidence; that policy is
unchanged, and initial OPF-030 plus NCX-001 is not combined. OPF-030 repair never
edits NCX. Real fixtures prove that an initially unreported NCX mismatch appears
after fixing the reference and then fails revalidation, without publishing any
output or starting a second repair pass. A matching NCX remains byte-for-byte
unchanged and the corrected EPUB validates successfully.

Failures include `opf030_evidence_ambiguous`, `opf030_package_ambiguous`,
`opf030_unique_identifier_missing`, `opf030_identifier_missing`,
`opf030_identifier_ambiguous`, `opf030_identifier_id_invalid`,
`opf030_identifier_value_invalid`, `opf030_reference_already_exists`,
`opf030_conflicting_repair`, `unsafe_opf030_archive` and `unsafe_opf030_mutation`.
Existing archive-wide refusal precedence and result schemas remain unchanged.
The normal rebuild, real EPUBCheck and exclusive output creation are reused;
failed validation keeps `repaired=[]`, `output_file=None` and publishes nothing.
Original EPUBs are never mutated or overwritten. Service, MCP, temporary-download
delivery and dependencies are unchanged.

Identifier validation can be deferred until its reference resolves: a real
invalid-UUID fixture initially has only OPF-030 and the reference RSC-005, then
produces OPF-085 as a warning after retargeting. For this family, that explicit
invalid-identifier evidence fails the proof and blocks publication. No UUID
parser, generation or normalization is added. `final_valid` still preserves
the real validator's True value for a warnings-only report, while `repaired=[]`,
`output_file=None` and `reason=opf030_identifier_value_invalid` show refusal.
The general warnings policy and all other repair families are unchanged.

See [fixture evidence and reproduction](tests/fixtures/opf030/README.md) and
[focused tests](tests/test_opf030.py).

```sh
.venv/bin/python -m pytest -q tests/test_opf030.py
python3 -m src.main tests/fixtures/opf030/unresolved.epub /tmp/opf030-repaired.epub
java -jar vendor/epubcheck-5.4.0/epubcheck.jar /tmp/opf030-repaired.epub
```

Choose an absent output path; existing files are not replaced.

## Optional privacy-preserving telemetry

Telemetry observes `service.inspect_epub()` and `service.repair_epub_file()`;
CLI and MCP callers using this boundary are covered. Direct low-level engine
calls are not instrumented. Engine safety, diagnosis, output and return contracts
are unchanged. No additional dependency is required.

Collection is **disabled by default**. Both variables are required to enable it:

```sh
export EPUB_DOCTOR_TELEMETRY_ENABLED=1
export EPUB_DOCTOR_TELEMETRY_PATH=/tmp/epub-doctor-dev/events.jsonl
```

Use a dedicated writable path outside the source tree for deployment. Set
`EPUB_DOCTOR_TELEMETRY_ENABLED=0` (or unset it) to disable collection; no telemetry
file lookup or version lookup occurs when disabled. Tests use temporary paths.
The configured directory is created on first write. New files use mode `0600`;
existing file permissions are not changed.

Each JSONL line contains exactly these schema-v2 fields:

```text
schema_version, event_id, timestamp, action, operation_completed,
initial_epubcheck_codes, final_epubcheck_codes, confirmed_safe_repairs,
original_valid, repair_attempted,
repair_success, final_valid, refusal_reason, fatal_count, error_count,
warning_count, file_size_bucket, processing_time_ms, epub_doctor_version,
epubcheck_version
```

IDs are random UUID4 values; timestamps are UTC. Elapsed milliseconds use a
monotonic clock. Codes are sorted, unique `AAA-000` technical IDs. Diagnoses and
refusal reasons use explicit known engine-enum allowlists. Events never copy
whole reports/results, messages, locations, paths, filenames, metadata, detail,
identifiers, content, URLs, user/account/IP information or EPUB hashes.
File sizes use binary MiB boundaries: `[0,1)`, `[1,5)`, `[5,20)`, `[20,50)` and
`[50,infinity)` MiB, labeled `<1MB`, `1-5MB`, `5-20MB`, `20-50MB`, `>50MB`.
The last label includes exactly 50 MiB. Unavailable values are JSON `null`.

`operation_completed` distinguishes normal service returns (including refusals)
from propagated exceptions; it does not mean repair succeeded. `repair_success`
requires successful validation, nonempty published repairs, an output and no
refusal. `already_valid` is not a repair success. `repair_attempted` is true when
success or failed revalidation proves an attempt, false for proven early exits,
and null when the existing result cannot establish the mutation stage. Inspect
uses null for both repair fields.

Schema version 2 replaces the ambiguous v1 `epubcheck_codes` field; no repository
consumer requires that legacy field. Both new fields exist in every event:

- `initial_epubcheck_codes`: sorted unique codes from the initial validation.
  Null means that validation did not return usable evidence (or observation failed).
- `final_epubcheck_codes`: sorted unique codes from the actual revalidation.
  Null means no usable revalidation evidence; an empty list means revalidation
  returned a report with no matching codes. Inspect always uses null here.

Refusals before revalidation and `already_valid` preserve initial codes and use
null for final codes. Successful repairs and failed revalidation retain both
sets independently. `remaining_messages` is never used as validation-stage evidence.
The two existing validator calls notify a private optional observer inside the
repair pipeline. Service retains only sanitized code lists in a per-call local
context. Public `repair_epub` and service results receive no additional fields;
there is no global/thread-local state and no additional validation, diagnosis,
EPUB parsing or repair. Observer exceptions are isolated inside the engine as
well as at the service boundary. With telemetry disabled no observer is passed.

Inspect counts describe its validation report. Repair counts remain null because
the engine does not expose counts. Doctor version comes from installed distribution
metadata, or null when unavailable. Checker version is null: the current result
has no trustworthy runtime version field, and telemetry does not infer it from a
directory name or execute another checker.

Observation, configuration, size/version lookup, serialization and writes are
best effort. Observer exceptions are suppressed separately from the operation;
original service exceptions are re-raised unchanged, with no exception text in
events. Failed collection is silent and can lose events. The writer is isolated
in `src/telemetry.py::write_event` for later backend replacement.

A process-wide thread lock covers rotation/append/open/close to prevent interleaved
lines within one process. The writer rotates before an append would exceed
`EPUB_DOCTOR_TELEMETRY_MAX_BYTES` (default 10485760, 10 MiB), keeping
`EPUB_DOCTOR_TELEMETRY_BACKUP_COUNT` archives (default 5). Both must be positive
integers. `events.jsonl.1` is the newest archive; the oldest is deleted on rotation.
New files are owner-only. With the defaults, new logs occupy at most 60 MiB.
Oversized individual events, invalid settings and rotation failures drop telemetry
without failing the EPUB operation. Disabled telemetry performs no rotation.

This is size-based retention, with no age-based deletion: low-volume events can
remain indefinitely. Existing oversized files are preserved as an archive until
rotations discard them; when reducing the backup count, remove surplus numbered
archives separately. Restrict directory access and manage any promised time-based
retention, copies and backups externally. Use one writer process per path and do
not run external rotation on the same files concurrently. Multiple processes
sharing a path, crash durability and ingestion monitoring are not provided.
Local telemetry I/O is synchronous. Event timestamps/UUIDs identify events,
not users or books. Future engine enums must be deliberately added to the
telemetry allowlists; unknown values are omitted/null.


## EPUBCheck execution timeout

Every EPUBCheck invocation has a 60-second timeout (`EPUBCHECK_TIMEOUT_SECONDS`
in `src/validator.py`), including initial validation and repair revalidation.
Python's `subprocess.run` kills and waits for the Java child on timeout. The
validator raises `EPUBCheckError` with a fixed technical message. Java stdout and
stderr go to `DEVNULL`, including on execution failure; only the exit code is
reported. Its temporary report directory is cleaned up.
Inspect propagates this error; repair returns the existing `epubcheck_failure`
result, removes temporary repair files and publishes no output. There is no retry.

This limit is per checker invocation, not a total request deadline: a repair can
run two validations. Process creation may delay timeout delivery.

The validator rejects JSON reports above 32 MiB (`MAX_EPUBCHECK_REPORT_BYTES`)
before `json.load`. This bounds report input bytes, not the exact memory occupied
by decoded Python objects. Java can still write a larger report before exiting;
production's 128 MiB `/tmp` tmpfs bounds temporary storage, and the validator
removes its report directory on success and failure.

## Uncompressed archive limits

Core repair checks every `ZipInfo.file_size` before diagnosis, entry reads or
mutation: each entry must be at most 64 MiB (`MAX_UNCOMPRESSED_ENTRY_BYTES`), and
their sum at most 256 MiB (`MAX_TOTAL_UNCOMPRESSED_BYTES`). Exact limits are
accepted. These checks use declared sizes without decompressing the input;
they are safety gates, not independent EPUB validation or repair evidence.
Repair refuses larger archives with `archive_entry_too_large` or
`archive_total_too_large`, leaves the source unchanged and creates no output.
Diagnosis applies the same gate before reading entries for supported repairs;
inspection propagates `UnsafeZipError` for oversized archives in that path.
Existing bounded XML reads and local/central ZIP consistency checks remain.
Rebuilding still reads one accepted entry at a time; these limits complement
Java heap and container memory caps rather than guaranteeing a fixed process RSS.


## Concurrent EPUB operations

A shared process-local admission limit covers service inspect and repair for the
whole synchronous pipeline, including diagnosis, mutation and both validations.
ChatGPT file tools acquire the same slot before downloading input and retain it
through output delivery/publication and workspace cleanup. Nested adapter/service
calls in the same thread reuse their slot; this is thread ownership only, not a
queue or shared validation state. Slots are released on success and exceptions.

Default capacity is **1**. Configure before starting the process:

```sh
export EPUB_DOCTOR_MAX_CONCURRENT_OPERATIONS=1
```

Only positive integers are accepted; invalid settings fail startup rather than
disable protection. Restart to change capacity. At capacity, calls fail immediately
with `ServerBusyError` at service level and `server_busy` MCP tool errors. No
unbounded waiting queue, download or Java process is started for rejected calls.
This admission rejection does not produce a repair result or a telemetry event.
Successful/refused admitted operations keep their existing results and telemetry.

Run one server process on the VPS. A limit of N in each of W processes permits up
to N * W operations; this is not a host-wide or distributed limiter. Low-level
engine/validator calls bypass service admission. The existing download store
also requires a single owner process. Completed-file download routes remain
available while EPUB processing is busy. Capacity does not cap memory per JVM;
choose capacity after measuring memory and configure deployment memory limits
separately. There is no Celery, broker or additional dependency.


## Java heap and deployment memory limits

The validator always launches `java -Xmx512m -jar ...` for both initial and final
validation. The default is defined by `EPUBCHECK_MAX_HEAP` in `src/validator.py`;
there is no arbitrary JVM-argument environment override. This limits Java heap,
not the total resident memory of the JVM, Python service or child processes.
Timeout and process-local concurrency admission continue to apply. A checker
execution/report failure retains the existing `EPUBCheckError` /
`epubcheck_failure` behavior and cannot publish an unvalidated output.

Production must additionally enforce a container or systemd cgroup memory limit
covering the server and its children. The Docker Compose configuration below
sets a container limit; no live VPS deployment limit has been applied.
An illustrative systemd service drop-in for capacity 1 is:

```ini
[Service]
MemoryAccounting=yes
MemoryHigh=768M
MemoryMax=1G
```

These are example deployment values, not measured sizing recommendations.
Leave headroom for native JVM memory, Python, ZIP processing and all concurrent
operations; do not set the total service limit equal to the Java heap limit.
`MemoryHigh` applies pressure/throttling; `MemoryMax` is the hard last line of
defense and can trigger OOM termination. The server itself can be killed by the
cgroup limit, so an HTTP error response or graceful cleanup is not guaranteed
in that case. Deployment restart/temporary-file cleanup policies must account
for abrupt termination. Increasing concurrency requires re-evaluating the total
memory budget.

References: [Java -Xmx](https://docs.oracle.com/en/java/javase/22/docs/specs/man/java.html),
[systemd memory controls](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html).


## Docker Compose and Nginx

The image includes Python 3.12, Java 17 and the complete bundled EPUBCheck JAR,
libraries and licenses. It pins the currently tested MCP SDK to 2.3.0. Runtime
uses UID/GID 10001, one server process, a read-only root filesystem and no Linux
capabilities. `.dockerignore` allowlists only runtime source and bundled checker
files: EPUBs, reports, credentials, local configuration, tests and git history
are excluded from the build context. No native user file is baked into the image.

```sh
cp .env.example .env
# Set EPUB_DOCTOR_PUBLIC_ORIGIN to your actual HTTPS origin, without a path/port.
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8080/healthz
```

The provided Compose command supplies `--public-origin` to enable delivery and
readiness. Standalone `docker run` must supply that option with the HTTP command
as well; the image does not invent a public hostname.

Only Nginx's port 8080 is published, on host loopback. Put an HTTPS reverse proxy
or secure tunnel in front of `http://127.0.0.1:8080` and preserve the public Host
and Origin headers. Certificates/TLS are managed by that outer proxy. Configure
ChatGPT with `https://YOUR-HOST/mcp`; output links use the explicit configured
HTTPS origin. Do not expose the app container port directly or scale app replicas.
The server remains unauthenticated; rate limits are not authentication.

Nginx accepts only `/mcp`, `/download/…` and `/healthz`. MCP requests have a 64 KiB
body limit (file references, not raw EPUB uploads), per-peer 2 requests/second
with burst 10, and a shared 5 requests/second limit with burst 20. Downloads use
burst 5. Excess requests return HTTP 429 without queueing. Concurrent connections
are limited to 8 per peer for MCP and 4 for downloads. The service's separate
nonblocking capacity-1 guard still protects the complete heavy pipeline.
The proxy preserves MCP headers/status and disables response buffering for
streamed downloads; MCP upstream inactivity timeout is 240 seconds, not a total
request deadline. App and proxy access logs are disabled. Nginx error logging is
also disabled because errors can include bearer-token download URLs; app logs
remain available with bounded Docker log rotation.

Rate-limit keys use the actual connecting peer, not untrusted forwarded headers.
Behind a single TLS proxy/tunnel or Docker gateway, users may share one peer
bucket; this configuration does not guarantee a per-user rate limit. For per-user-IP
limits, explicitly configure Nginx's real-IP module with only the trusted proxy
addresses, or apply those limits at the outer TLS proxy. Never trust forwarded
addresses from arbitrary clients. The shared global limit remains in force.

`GET /healthz` returns 200 only after app lifespan startup with an open download
store, an available checker JAR and a Java executable; otherwise 503. It performs
no validation and stays responsive while the heavy-operation slot is occupied.
This is lightweight readiness, not proof that a real repair succeeds. Compose
waits for app health before starting Nginx; health failures after startup mark a
container unhealthy but do not themselves restart it. Exit/crash restart policy
is `unless-stopped`.

Compose fixes concurrency to 1, app memory/swap ceiling to 1 GiB, app CPU to 1,
and PID limit to 128; Nginx gets 64 MiB and CPU 0.5. Java heap remains 512 MiB.
App `/tmp` is a 128 MiB tmpfs charged to container memory; Nginx uses 16 MiB.
These are initial budgets, not measurements for your VPS. Adjust them against
available host RAM and native JVM/Python memory usage before public deployment.
The named `app-data` volume retains private repaired outputs and opt-in telemetry
under `/data`; outputs expire via the existing cleanup policy. Do not share this
volume with multiple app instances. Restart does not guarantee preservation of
in-flight input workspaces. `docker compose down` retains the volume; `down -v`
permanently removes stored outputs and telemetry. Docker image tags and transitive
Python dependencies should be locked to reviewed digests/versions for repeatable
release builds; there is no new application dependency or Celery service.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main` and pull requests. It uses
Python 3.12 and Java 17 on Ubuntu 24.04, installs the tested MCP SDK 2.3.0 and
pytest, runs the full regression suite, validates Compose with an example public
origin, then builds the production Docker image. The job has a 45-minute limit
and read-only repository permissions. New updates cancel an older run for the
same branch/PR. Configure the `test-and-build` check as required in GitHub branch
protection if merges must wait for CI success.

The workflow uses the documented v7 interfaces of
[checkout](https://github.com/actions/checkout) and
[setup-python](https://github.com/actions/setup-python). Actual hosted CI results
are available only after the workflow is pushed and GitHub Actions runs it.
