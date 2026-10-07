# Temporary repaired-EPUB delivery

This is an external temporary HTTPS download link, **not a native ChatGPT
attachment**. The user has already confirmed the isolated link PoC worked in
the real ChatGPT UI. Production code does not import or depend on that PoC;
it remains a separate development utility serving public text only.

## Architecture and configuration

```
ChatGPT fileParams → existing secure downloader → private input workspace
  → existing repair service → EPUBCheck revalidation
  → only successful final_valid=True output → private temporary download store
  → HTTPS capability link → GET /download/<token> → user downloads EPUB
```

The six repair families and input downloader are unchanged. The official Python
MCP SDK 2.3 API (`MCPServer`, `tool`, `custom_route`, `streamable_http_app`)
provides the MCP application. Uvicorn runs that same SDK ASGI app with access
logs disabled. Starlette/Uvicorn are already SDK dependencies; no additional
dependency, HTTP framework, database, queue, object storage or account system
was added. Exactly two MCP tools remain: `inspect_epub` and `repair_epub`.

Delivery requires both `--transport streamable-http` and `--public-origin`.
Without delivery configuration, stdio and the previous HTTP mode retain their
explicit `unavailable` result and delete workspace outputs. The local-path MCP
adapter and CLI are unchanged.

The origin must be an explicit HTTPS ASCII DNS origin on port 443, without
credentials, path, query or fragment. Loopback/numeric IP/local names are
rejected. It is canonicalized and added to the existing MCP Host/Origin
allowlists. Neither `Host` nor forwarded headers determines returned URLs.
DNS/domain ownership and TLS termination remain operator responsibilities;
validation is not a certification of an operator-configured host.

`--download-directory` defaults to `/tmp/epub-doctor-downloads-<uid>` and must
be a dedicated, existing-parent, private directory owned by the process user,
mode `0700`. The store creates it if missing. Files are `0600`; root, lock,
metadata and EPUB opens refuse symlinks. Storage uses fixed suffixes and opaque
tokens, never a user filename, OpenAI file ID or URL. This filesystem MVP targets
Linux/POSIX (`flock`, directory descriptors, `O_NOFOLLOW`, hard links); it is not
a Windows portability implementation. Parent directories and same-UID local
processes are trusted. Use a dedicated runtime user for isolation.

One process exclusively owns a root with `flock`; a second instance fails
cleanly. Threads/concurrent repairs share a lock. Do not share a root between
multiple servers/workers. Root lifetime is owned by the CLI; embedded users must
close the store after the ASGI app shuts down.

## Publication, limits and failures

Publication requires all of:

- `final_valid is True` (the boolean, not another truthy value);
- a nonempty `repaired` list and `reason is None`;
- the service's exact expected workspace output path;
- an existing nonempty regular output opened without following a symlink.

The input limit does not substitute for an output limit: rebuilding a ZIP can
change compressed size. `MAX_OUTPUT_BYTES` is explicitly 20 MiB. A root permits
at most 100 published files and 200 MiB of published bytes. These constants are
centralized in `src/temporary_downloads.py`. One bounded staging copy can
temporarily add up to another 20 MiB. Quotas bound this store, not input workspace
concurrency, network bandwidth or copies already downloaded by users.

The store copies into a private exclusive `.pending-<token>` file, closes and
fsyncs it, then publishes bytes with an atomic, no-overwrite hard link. A second
bounded, fsynced JSON file containing creation/expiry/size/safe display name is
atomically linked as the **ready marker**. The directory is fsynced. An endpoint
requires both valid metadata and matching regular EPUB bytes. A staging file or
an EPUB without its marker cannot be served. The publisher opens the same
download object successfully before reporting `delivered=True`.

No unvalidated intermediate is published. Disk-full/permission/copy/marker
failure rolls back owned files and returns delivery `failed`, never a URL.
Failure during rollback is logged without identifiers; sweeping retries cleanup.
Oversize and quota failures return `output_too_large` and
`storage_capacity_reached`. Other expected I/O failures return
`publication_failed`; unexpected programming errors are not broadly caught.
Startup configuration/storage failure exits cleanly before starting HTTP.
Engine failures/refusals remain normal service results, with no publication.

## Result schema

Inspection preserves the eight service fields without reinterpretation.
Repair retains the existing `repair` / `file_output` envelope. The core service
result is not mutated: all repair fields and optional `detail` are preserved,
except its internal workspace `output_file` is projected to `null` as before.

Successful configured delivery:

```json
{
  "repair": {
    "original_valid": false,
    "detected": ["manifest_missing_required_property"],
    "repaired": ["manifest_missing_required_property"],
    "final_valid": true,
    "output_file": null,
    "remaining_messages": [],
    "reason": null
  },
  "file_output": {
    "status": "available",
    "delivered": true,
    "download_url": "https://YOUR-HOST/download/OPAQUE-TOKEN",
    "expires_in_seconds": 900,
    "file_name": "book-repaired.epub"
  }
}
```

`delivered=True` means a downloadable object was published, not that a browser
has already downloaded it. `unavailable` (disabled delivery or no qualifying
repair) and `failed` (publication failure) return `delivered=False` and `detail`,
with **no** URL/expiry/filename fields. Do not turn a successful repair with
failed delivery into a successful download claim. MCP input/download failures
retain their existing clean tool-error behavior. The repair tool explicitly
declares `readOnlyHint=false`, `destructiveHint=false`, `openWorldHint=true`.

## Security and lifecycle

- `secrets.token_urlsafe(32)` generates 256 bits of randomness, encoded as 43
  URL-safe ASCII characters. At most ten collision attempts; existing files are
  never overwritten. Tokens contain no filename, input ID or path.
- This is **bearer/capability access**, not user authentication. Anyone holding
  the URL may make repeated downloads until expiry. Distinct tokens select
  distinct objects; there is no user/account binding. Sharing a URL shares access.
- `DOWNLOAD_TTL_SECONDS=900` starts after copying succeeds. Absolute creation
  and expiry timestamps are persisted; restart does not renew retention.
  Malformed/oversized/corrupt metadata, future creation times, missing files,
  wrong sizes and symlinks fail closed and are swept. System-clock changes can
  invalidate objects early; keep the server clock synchronized.
- Startup, every 30 seconds while the HTTP app runs, and before publication
  sweep expired/corrupt/orphaned/pending files. Access to an expired object
  rejects it and attempts deletion immediately. New downloads stop at expiry;
  normal disk deletion occurs by the next sweep (about 15 minutes plus up to
  30 seconds). A request that already holds an open descriptor may finish after
  unlink/expiry; deletion does not revoke bytes already being transferred.
  Transfers have a 60-second ASGI deadline, so stalled clients do not hold deleted
  output descriptors indefinitely. Timeout/disconnection closes the descriptor;
  a partial transfer is incomplete (the declared Content-Length is unchanged).
- During a stopped/crashed process or persistent disk/permission failure,
  timed deletion cannot be guaranteed. Startup removes stale objects once the
  store is usable. `/tmp` is not promised to auto-delete on every host. Operators
  must ensure restart/monitoring and a working private writable store.
- The original EPUB, signed input URL and `file_id` are not persisted in this
  store. Input and workspace output retain existing context-manager cleanup
  after success/refusal/failure. A validated published copy survives workspace
  removal and is retained only for download. A crash can leave the existing
  input temporary workspace behind; this task does not add crash recovery to
  the frozen downloader or promise input erasure during a process crash.

`GET /download/<token>` returns exact output bytes, `application/epub+zip`,
`Content-Disposition: attachment; filename="<safe-display-name>"`, `Content-Length`,
`Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and
`X-Content-Type-Options: nosniff`. Malformed/unknown/expired objects uniformly
return 404. There is no path lookup API, directory listing, range/resume or
delete tool. Simultaneous downloads hold independent descriptors; cleanup can
unlink an object without redirecting a transfer to another file. Lost/deleted
objects fail closed. No partially copied bytes are exposed.

`book.epub` becomes `book-repaired.epub`. Only conservative ASCII names of at
most 120 input characters are accepted. Paths, slash/backslash, drive prefixes,
controls/NUL, non-ASCII names, excessive length, dot-only names, double dots,
and missing `.epub` fall back to `repaired.epub`. The display name is never used
for a physical storage path or inserted unsanitized into a header.

## Logs, privacy and public distribution

The configured entry point explicitly disables Uvicorn access logs because the
path contains a secret token. Expected store/delivery errors and sweep warnings
contain only generic codes/messages, no token/path/input URL or EPUB contents.
Existing service diagnostic messages/details retain their existing semantics.
Do not enable
SDK DEBUG logs, middleware logging of MCP arguments/results, or reverse-proxy /
tunnel request-path logging: those are outside this application's suppression.
Capability URLs are intentionally in the tool result/conversation and can also
remain in browser history, copies, downloads or infrastructure logs. Headers
reduce caching/referrer leakage; they cannot retract shared links or delete
user/OpenAI/browser copies. Actual tunnel/hosting recipients, data geography,
retention and deletion handling require an accurate privacy policy and review.
No secondary content use, analytics or account system is implemented.

**Verdict B: technically usable for the authorized development MVP, not yet
ready for public production/plugin review.** Official [authentication guidance](https://developers.openai.com/plugins/build/auth)
advises authentication for customer-specific data and write actions and expects
OAuth 2.1 for authenticated MCP servers. It does not establish that a short-lived
capability URL satisfies that advice for private EPUBs. MCP OAuth and a separate
browser/user-bound download policy require a product/security decision before
public rollout; MCP OAuth alone would not authenticate this custom route.
No definitive rule specifically requiring OAuth on this browser endpoint was
found; do not invent one. The [plugin guidelines](https://developers.openai.com/plugins/plugin-guidelines)
and [security/privacy guidance](https://developers.openai.com/plugins/guides/security-privacy)
require truthful privacy disclosures and data-handling controls. No-auth custom
connection support and a live clickable link are not public-review approval.

## Exact live ChatGPT test

The following is a development tunnel, not deployment. From the repository root:

```sh
cd /home/omid/Desktop/projects/epub-doctor
.venv/bin/python -m pip install -e '.[test]'
java -version
```

Terminal 1 — obtain an actual public hostname (keep this tunnel running):

```sh
cloudflared tunnel --url http://127.0.0.1:8000
```

Terminal 2 — replace the example with the **actual** HTTPS origin printed by
cloudflared; start the real adapter, not the PoC:

```sh
cd /home/omid/Desktop/projects/epub-doctor
EPUB_DOCTOR_PUBLIC_ORIGIN='https://REPLACE-WITH-ACTUAL-HOST.trycloudflare.com'
.venv/bin/python -m src.chatgpt_mcp_server \
  --transport streamable-http --host 127.0.0.1 --port 8000 \
  --public-origin "$EPUB_DOCTOR_PUBLIC_ORIGIN" \
  --download-directory /tmp/epub-doctor-downloads-live
```

Set/update/recreate the development Custom MCP connection URL to
`https://ACTUAL-HOST.trycloudflare.com/mcp`. Select No authentication **for this
authorized development test only**. If the tunnel hostname changed, the old
connection and old links point to the wrong host. Restart the adapter after
configuration changes, refresh tool definitions and start a new conversation.
Attach/select EPUB Doctor, upload this actual repository fixture:

```
tests/fixtures/opf014/package-manifest-prop-scripted-undeclared-javascript-error.epub
```

Ask exactly: `Repair this EPUB with EPUB Doctor.` Confirm the write tool if the
host prompts. Expect `final_valid=true`, a nonempty `repaired` list, and
`file_output.status=available`, `delivered=true`, a 900-second HTTPS download
URL. Click it before expiry. Save the actual downloaded file, then independently
check it (replace the pathname with its real download location):

```sh
java -jar vendor/epubcheck-5.4.0/epubcheck.jar \
  "$HOME/Downloads/package-manifest-prop-scripted-undeclared-javascript-error-repaired.epub" \
  --json /tmp/epub-doctor-live-epubcheck.json
```

Expect exit 0 and no fatal/errors. A second click before expiry should work;
after 15 minutes a new click should return 404. Keep the server running another
30 seconds and verify the dedicated directory has no corresponding `.epub` /
`.json` object (only `.lock` remains if there were no other repairs). Do not post
the secret URL/token in logs or test reports. Stop tunnel/server after testing.

Automated real-service/independent-checker smoke without external networking:

```sh
.venv/bin/python -m pytest -q tests/test_chatgpt_download_delivery.py -k real_opf014
.venv/bin/python -m pytest -q
git diff --check
```

The automated integration test uses the official SDK HTTP app, a fake input
HTTPS response containing the real fixture, the real repair service, actual
download endpoint and an independent EPUBCheck run on downloaded bytes. It does
not claim a new live ChatGPT repair/download occurred; that requires the manual
procedure above. The earlier clickable-link PoC is already user-confirmed.

Verification on 7 October 2026: **725 tests passed in 584.17 seconds**, including
all 622 previous tests and 103 new tests (74 store tests, 29 delivery tests).
All six existing repair families and the isolated 15-test PoC remain passing.
`git diff --check` was clean. An additional real CLI/loopback HTTP smoke started
the configured server, discovered exactly two tools, downloaded a genuinely
repaired OPF-014 fixture byte-for-byte, independently validated it with EPUBCheck,
and verified the capability token was absent from captured server logs. The
smoke server was stopped and its temporary files removed.
