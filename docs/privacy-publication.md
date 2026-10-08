# Publishing the privacy notice

[PRIVACY.md](../PRIVACY.md) is the public-facing English draft. It describes the
actual source behavior and default disabled telemetry, not an already-audited
live service. The README links to it; no public HTTP page has been deployed.

Complete the operator/controller identity, privacy contact, hosting location,
TLS proxy/tunnel providers, legal basis, applicable rights/complaint details,
operational-log retention and backup practices using actual deployment facts.
Then remove the draft banner and fill all blank fields, review the completed notice and
publish it at a stable public HTTPS URL linked from the service's entry point
and any required platform listing. The operator owns that final deployment.

Keep these distinctions intact:

- The 900-second output TTL denies new downloads; periodic cleanup (30 seconds)
  and interrupted service can make physical storage retention longer. An opened
  stream may finish after expiry. Do not promise exact 15-minute physical deletion.
- The temporary output metadata contains a sanitized display filename and exact
  byte size; application telemetry does not. Inspection results may contain book
  metadata and full validator messages returned to the client.
- Container workspaces use tmpfs, but published outputs and optional telemetry
  use a persistent named volume. Crashes, snapshots and backups must be addressed
  by the operator. Do not promise secure erasure or backup exclusion without proof.
- Application telemetry defaults to disabled. JSONL rotates at 10 MiB with five
  archives by default, deleting the oldest on rotation, but has no time-based retention
  mechanism. Keep it disabled for release unless a real deletion policy is
  implemented and tested. A proposed retention period is not an operational fact.
- Size-based Docker log rotation is not a time-based retention promise. Outer
  proxy/provider IP logging is not controlled by the telemetry allowlist.

For a future enabled-telemetry release, choose and document a limited raw-event
retention period starting from each event's UTC timestamp. No period is adopted
or enforced by this documentation change. Implement deletion across active
files, rotated files and any copies/backups before promising that period publicly;
state separately whether aggregate statistics survive deletion of raw events.

Evidence checked: `src/chatgpt_files.py::downloaded_epub`,
`src/temporary_downloads.py` (TTL, cleanup, publication metadata and expiry),
`src/chatgpt_mcp_server.py` (responses and sweep), `src/telemetry.py` (schema 2),
`docker-compose.yml`, `.env.example`, and `deploy/nginx/nginx.conf`.

Reference for required EU transparency information:
[GDPR Article 13](https://eur-lex.europa.eu/eli/reg/2016/679/oj), including controller
identity, purposes/legal basis, recipients and storage periods/criteria.
