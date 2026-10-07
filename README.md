# EPUB Doctor

Small Python engine for deterministic repairs. Bundled EPUBCheck 5.4.0 is the
validation authority; Python diagnoses its reports rather than validating EPUB
content independently. Requires Python 3.10+ and Java on PATH.

The ChatGPT adapter accepts uploaded-file references over Streamable HTTP.
With an explicit public HTTPS origin, successful validated repairs return an
external temporary download link, available for 15 minutes. This is not a native
ChatGPT attachment. See [delivery, security, retention and exact live-test commands](docs/temporary-epub-delivery.md).
Authentication/privacy/plugin-review decisions remain before public production.

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
Core/service files, ZIP checks, seven repair families and validation semantics
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
operation. These limits concern downloaded input bytes, not expanded ZIP size
or EPUBCheck runtime; the existing core safety policy remains intact.

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
no authentication and binds loopback by default. No deployment is included. ChatGPT needs
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
