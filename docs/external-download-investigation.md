# External download-link investigation — 7 October 2026

**Status update:** the user subsequently confirmed the real ChatGPT PoC link was
clickable and downloaded the exact expected text. The investigation below is
the historical pre-PoC decision record, not the current implementation status.
Temporary repaired-EPUB delivery is now implemented separately; see
[architecture, safeguards and live test](temporary-epub-delivery.md).
The current verdict is B pending authentication/privacy/public-review decisions.

**Verdict C: prepare a minimal live PoC before deciding.** No blanket ban on
task-related storage or external links was found in the official sources below.
That is not approval of an anonymous capability-download product. Clickable-link
behavior for this custom MCP connection still needs a real ChatGPT UI test.

Only the isolated PoC is implemented. Existing repair tools, temporary-file
cleanup, `delivered=False`, downloader protections and six repair families remain
unchanged. There is no retained EPUB, download token, TTL store or deployment.

## Evidence and its limits

### Officially documented facts

- [Plugin guidelines](https://developers.openai.com/plugins/plugin-guidelines#privacy):
  use task-necessary data only. A submitted plugin needs a published privacy
  policy covering data categories, purposes, recipients, retention and controls.
  Restricted-data rules also apply to uploaded content.
- [Security & Privacy](https://developers.openai.com/plugins/guides/security-privacy#data-handling):
  define and publish retention, respect deletion requests, redact PII in logs,
  and keep responses minimal. These are storage requirements, not a prohibition
  on storage.
- [Correct annotation](https://developers.openai.com/plugins/plugin-guidelines#correct-annotation):
  persisting artifacts is a write (`readOnlyHint=false`). Use explicit boolean
  annotations and accurately disclose side effects. Labels do not replace
  authorization or required confirmation of irreversible operations.
- [Authentication](https://developers.openai.com/plugins/build/auth#authenticate-your-users):
  customer-specific data and write actions should authenticate users. Authenticated
  MCP servers are expected to use OAuth 2.1. The guide does not establish that
  an unguessable URL satisfies this advice for a private EPUB download endpoint.
- [Custom MCP connection](https://developers.openai.com/api/docs/guides/custom-mcp-server):
  no-auth connections are supported. Connection support alone does not establish
  eligibility of a particular data-storage/download design for publication.
- [Reference](https://developers.openai.com/plugins/reference#tool-results):
  tool `content` and `structuredContent` reach the model/conversation.
  `window.openai.openExternal` is a documented widget API for opening vetted links;
  its redirect-domain configuration is widget-specific, not a rule for plain
  model-rendered URLs. We do not implement a widget.
- [Submission](https://developers.openai.com/plugins/deploy/submission):
  public distribution involves identity/domain verification, tool scans, review
  cases and a demo recording. A development probe is not a finished Directory
  submission or evidence of review acceptance.

### Questions answered conservatively

| Question | Finding |
| --- | --- |
| Temporarily process input on our backend? | Inference: compatible with task-limited processing and accurate disclosure, subject to data restrictions and applicable obligations. |
| Temporarily retain generated output? | Storage is contemplated; retention/deletion obligations apply. No special permission for our proposed 900-second store was found. |
| Return HTTPS URL? | MCP can serialize it, and OpenAI documents model-visible tool results and external widget links. No universal clickable-result guarantee was found. |
| Direct download versus landing page? | No blanket requirement to use a landing page was found. Whether the direct attachment response works in this UI/browser is what the probe tests. |
| Special metadata? | No documented file-output metadata for this ordinary link was found. `openai/fileParams` remains input-only. Do not invent output metadata. |
| Write classification/confirmation? | Retained output is a write. The PoC is read-only because it returns only fixed public text. Future retention must be described and use honest annotations; host confirmation must be checked live. |
| Authentication for download? | Auth guide advises authentication for user-specific data/writes, but does not specify a rule for this exact browser endpoint. No-auth development support is not approval of capability-only access. |
| Capability URL acceptable? | Unresolved policy/product decision. It authorizes any holder, not an identified user. Seek clarification before public sensitive-data use; a link-rendering success does not settle this. |
| Storage location/subprocessors? | The reviewed pages do not prescribe one storage region or a complete subprocessor checklist. Identify actual hosting/tunnel/log recipients and relevant geography in the real privacy/security assessment; do not invent a residency promise. |
| Prescribed privacy wording? | Required disclosure topics are specified; no mandatory sentence or 15-minute wording for this design was found. |

### Protocol, live evidence, and inference are separate

Protocol: the SDK can return a URL in structured JSON and text content, and
register a custom HTTP route. Installed official Python MCP SDK is 2.3.0;
`MCPServer.custom_route` explicitly creates public, unauthenticated routes.
This is suitable for our harmless probe. A future private download handler would
need its own access checks; MCP OAuth must not be assumed to protect that route.

Existing live evidence **reported by the project owner** proves file input,
repair and revalidation in ChatGPT. It does not prove URL rendering/download.
Our automated tests prove protocol behavior and the fixed HTTP response only.
No actual ChatGPT UI observation is claimed by this investigation.

Inference: a user-requested link returned in model-visible tool output is a
reasonable experiment. It should remain distinct from native attachment
delivery and from authorization/review approval of the full storage design.

## Isolated PoC

Module: `src/download_link_poc.py`. It exposes exactly one tool,
`get_download_link_poc()`, with no input arguments. Existing servers still
expose their original two tools and do not import or register this tool.

Tool result:

```json
{
  "url": "https://YOUR-ACTUAL-DEV-HOST/download-link-poc.txt",
  "file_name": "epub-doctor-link-poc.txt",
  "detail": "Development probe only: fixed public text, no EPUB or user data."
}
```

The example origin is a placeholder, never a built-in working URL. The operator
must configure the actual HTTPS origin; URLs are not derived from request Host
or forwarded headers. The route serves only these fixed ASCII bytes:

```text
EPUB Doctor HTTPS download-link proof of concept.
No EPUB or user data is stored or served.
```

It sets `Content-Type: text/plain; charset=utf-8`, a fixed attachment filename,
`Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and `nosniff`.
No filesystem read/write, user input, secret token or arbitrary-file route exists.
The endpoint is publicly readable harmless text; do not upload private data.
Normal server/tunnel access logging can occur, so do not describe this as
collecting no metadata or being a privacy-approved production service.

### Exact live procedure

Run in the repository root. Leave the real EPUB Doctor server untouched.

1. In terminal A, start a **separate development** tunnel to the probe's port:

   ```sh
   cloudflared tunnel --url http://127.0.0.1:8001
   ```

   Copy the actual `https://...trycloudflare.com` origin printed by the tool.
   This command uses the project's existing development workflow; no tunnel
   installation, Cloudflare configuration or production deployment is included.

2. In terminal B, substitute that real origin:

   ```sh
   .venv/bin/python -m src.download_link_poc \
     --public-origin https://YOUR-ACTUAL-HOST.trycloudflare.com \
     --host 127.0.0.1 --port 8001
   ```

3. Check the endpoint once in your browser, or with:

   ```sh
   curl -i https://YOUR-ACTUAL-HOST.trycloudflare.com/download-link-poc.txt
   ```

   Confirm the fixed two-line body and attachment header. This is an endpoint
   check, not the ChatGPT link test.

4. In ChatGPT Plugins, add a **separate** custom MCP server named
   `EPUB Doctor Link PoC`, URL `https://YOUR-ACTUAL-HOST.trycloudflare.com/mcp`,
   with **No authentication** for this harmless development probe. Install it.
   Verify discovery lists only `get_download_link_poc`. After changes, Refresh
   the connection and start a new conversation, following the
   [official test/refresh flow](https://developers.openai.com/plugins/deploy/connect-chatgpt).

5. Select `@EPUB Doctor Link PoC` and use this exact prompt, with no attachment:

   ```text
   Call get_download_link_poc with no arguments. Show the returned url unchanged
   as a clickable Markdown link labeled "Download the harmless PoC text file".
   Do not fetch the URL or use another tool. Say this is a development text-file
   probe, not a repaired EPUB or a native ChatGPT attachment.
   ```

6. Inspect the tool result: the URL must exactly match your configured origin
   plus `/download-link-poc.txt`. Click the link **in ChatGPT's answer**. Save/open
   the file and confirm the exact two-line text. Record any safety interstitial,
   URL rewriting, link blocking, missing attachment behavior or filename changes.
   Copy/paste into a browser alone is not a pass for clickable ChatGPT behavior.

7. Repeat in a fresh conversation and on a supported mobile client if available.
   Report: tool called, exact returned/displayed URL, clickable yes/no, opened or
   downloaded, filename/body, warnings, client/platform. Do not include private
   files or future capability tokens in evidence.

8. Stop both terminal processes with Ctrl+C when done. If the tunnel restarts
   with a new hostname, restart the probe with the new origin and update the test
   connection. Quick Tunnel is development-only; it is not the production URL.

Stop here. Successful link display/download supports the next design stage,
but does not prove a retained EPUB workflow, native attachment delivery,
capability-token authorization, TTL deletion or public review eligibility.

## Conditional next-stage engineering notes — not implemented

These are our proposed constraints, not OpenAI requirements or legal guarantees.
Do not implement them until the live probe and the access/privacy decision pass.

- **Storage boundary:** one private local output directory; no original inputs.
  Retain only service output with `final_valid is True`, a nonempty successful
  `repaired` result, and the expected regular output file. A service refusal,
  missing file or size failure produces `delivered=False` without changing core
  repair semantics.
- **Limits:** propose a separate 20 MiB output cap. ZIP reconstruction can change
  compressed size, so the 20 MiB input cap alone is insufficient. Measure actual
  output and bound publication/serving; refuse delivery if it exceeds the cap.
  Also require a total storage quota and concurrent-publication bound to prevent
  many valid requests exhausting disk. No unbounded file server.
- **Capability proposal:** `secrets.token_urlsafe(32)` provides 256 random bits.
  Tokens are server-created, fixed-format path components; no sequential IDs or
  user-controlled paths. Tokens isolate objects, not identities: anyone who
  obtains another user's URL can access that object until expiry. That limitation
  must be explicit if capability access is accepted. Multiple downloads during
  TTL are preferred; one-shot links are vulnerable to previews/scanners and retries.
- **Filenames:** physical name always `repaired.epub`. Use a separate, short,
  aggressively sanitized ASCII display name, or `repaired.epub`. Reject traversal,
  separators, controls and excessive length rather than passing them to headers.
- **Publish:** bounded copy to a private staging directory, restrictive permissions,
  expiry/display-name metadata, atomic publication to an exclusive token directory.
  Set availability only after publication succeeds and a configured download
  origin exists. Origin configuration/local readability do not prove public
  reachability: verify the stable external route in integration checks before
  enabling delivery. If copy, metadata or response construction fails, remove
  the unpublished/unreturned output where possible; stale cleanup is the fallback.
- **TTL:** persisted absolute expiry, reject new requests at expiry, startup sweep,
  periodic sweep about every minute, and lazy deletion on access. Lazy deletion
  alone leaves abandoned files indefinitely. Handle missing/corrupt metadata
  conservatively. No Celery, Redis or database is required for a single-process
  local MVP; constrain deployment to that mode until coordination is addressed.
- **Races/restarts:** open and validate a regular file descriptor before sending;
  do not follow symlinks or serve arbitrary paths. Cleanup must tolerate races
  and missing files. An already-started download may finish after expiry; deletion
  during downtime occurs on startup, not while the process is stopped. Unknown,
  malformed and expired tokens should share a generic 404 response. Test disk
  full, permissions, crashes, concurrent requests and restart sweeps.
- **HTTP:** HTTPS, `application/epub+zip`, safe attachment filename, `no-store`,
  `nosniff`, no directory listing, no redirects or analytics on the download route.
  `no-store` is appropriate to discourage compliant caches but cannot revoke a
  user's saved copy, stop screenshots/sharing, or guarantee deletion by intermediaries.
- **Leakage:** the URL is a bearer capability. It can remain in ChatGPT history,
  browser history, shares, extensions, request logs and proxy/tunnel systems.
  Redact download paths/tokens and signed input URLs at application and proxy
  layers; use no-referrer responses, while recognizing they do not retroactively
  remove an incoming Referer or prevent history/sharing. Never put such links into
  unrelated widget props or telemetry. Audit actual logging/backup behavior.

### Privacy disclosure draft for that future implementation

Use only once the behavior exists and has been verified; this is not a deployed
policy or a legal compliance determination:

> EPUB Doctor uses the EPUB you explicitly provide to validate and attempt safe
> repair. The processing workspace is removed when processing ends. A successfully
> validated repaired output is retained temporarily only to permit download.
> New download requests are rejected after 15 minutes. Expired outputs are removed
> by periodic cleanup while the service runs, or on startup after downtime. An
> already-started transfer may finish. If capability links are used, anyone with
> the link can download during its lifetime; treat it as confidential. EPUB
> contents are not used for unrelated purposes. Saved copies and conversation or
> browser history are outside this output store's deletion control.

Before publication, add the actual operator/contact, recipients/service providers,
hosting geography, log categories/retention, backup policy, deletion-request process
and user controls. Identify actual Cloudflare/hosting involvement rather than
claiming data never leaves the local server. No implemented behavior presently
supports a guarantee that all copies are deleted within exactly 15 minutes.

## Automated evidence

The focused tests cover SDK discovery/calls, model-visible URL preservation,
fixed HTTP bytes and headers, rejection of unrelated paths/methods, invalid
operator origins, enabled MCP Host protection, no file creation, and isolation
from existing MCP tool lists. They use only in-process transport, with no external
network. The actual ChatGPT UI portion remains pending owner observation.

A separate real loopback HTTP smoke test also passed initialization, tool
discovery/call, and exact fixed-byte download with the documented entry point.
Its configured public origin was a test placeholder; no public HTTPS or
ChatGPT UI success is inferred from that local check. The test server was stopped.

Verification for this change: **15 new focused tests passed**; the complete
suite passed **622 tests in 477.75 seconds**, including all 607 existing tests.
All six frozen repair families remain passing. `git diff --check` and whitespace
checks on the new files passed. No dependency was added.

```sh
.venv/bin/python -m pytest -q tests/test_download_link_poc.py
.venv/bin/python -m pytest -q
git diff --check
```
