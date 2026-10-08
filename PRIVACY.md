# EPUB Doctor Privacy Policy

Updated: 8 October 2026

**Publication status: draft for review.** This notice describes the supplied
ChatGPT/HTTP deployment with application telemetry disabled. The operator and
hosting details below must be completed and checked against the actual public
service before publishing. It does not certify any deployment's legal compliance.

## Who operates the service

- **Service operator / data controller (legal name and address):**
- **Privacy contact (email or URL):**
- **Hosting provider and processing location:**
- **HTTPS proxy/tunnel provider and processing locations:**
- **Applicable legal basis for requested file processing:**

## What happens to your EPUB

When you request inspection or repair through ChatGPT, EPUB Doctor receives a
file reference, including a download URL and file identifier. An optional
filename and MIME type may also be supplied. The service uses the reference to
download your EPUB over HTTPS into a private temporary processing workspace.
These references are not recorded in application telemetry.

The EPUB and its contents are read locally by EPUB Doctor and EPUBCheck to
inspect the file and perform supported repairs. EPUB Doctor's processing
pipeline does not call an external AI model or send the EPUB to a separate
validation service. The EPUB itself can contain personal information, such as
an author name or publication identifiers; processing necessarily reads the
file you provide.

After a request finishes, its temporary workspace, including the input and any
workspace output, is deleted during normal cleanup. This also applies when
repair is refused or a handled error occurs. Abrupt termination, storage errors
or a host crash can prevent immediate cleanup. The supplied container deployment
uses temporary memory-backed storage for workspaces, which is discarded when
the container is stopped or recreated. This is ordinary file deletion, not a
promise of forensic secure erasure.

Inspection and repair results are returned to the requesting client. They can
include EPUBCheck messages, internal EPUB paths and publication metadata from
your book. In ChatGPT, those results become part of the conversation. This is
separate from the restricted technical telemetry described below.

## Repaired files and download links

When temporary output delivery is enabled and repair succeeds, a separate copy
of the repaired EPUB is placed in private temporary download storage. The
original processing workspace is then deleted. An unsuccessful repair does
not produce a downloadable repaired file.

The download link is valid for **up to 15 minutes after publication**. It is a
capability URL: **anyone who has the complete link can download the file during
that window**, without signing in or proving ownership. It can be used more than
once before expiry. Keep the link private and do not post it publicly.

No new downloads are permitted after expiry. A download already started can
finish after the link expires. Copies saved by you or someone with the link are
outside EPUB Doctor's retention controls.

Expired copies and their temporary download metadata are deleted by cleanup
while the service is running, normally on the next 30-second sweep. Cleanup also
runs on startup and when appropriate during file access/publication. A stopped
service or cleanup failure can delay physical deletion until cleanup resumes.
**The 15-minute limit is an access window, not a guaranteed physical-erasure
deadline.**

Temporary download metadata includes creation/expiry times, the output's byte
size and a sanitized display filename for the download. Consequently, a filename
may be retained alongside the temporary output even though filenames are never
included in application telemetry. The repaired EPUB itself retains the book's
contents and metadata except for the supported repair changes.

## Application telemetry

Application telemetry is **disabled in the supplied public deployment
configuration**. With it disabled, EPUB Doctor writes no application telemetry
events and therefore retains no such events. This statement must be updated if
the operator changes that configuration.

If enabled, telemetry is intended to measure use, validation errors and repair
outcomes. It records only the following technical fields:

| Data | Meaning |
| --- | --- |
| Schema version | The event format version, currently 2 |
| Random event ID and UTC timestamp | An individual processing event and when it occurred; no stable user/book tracking ID |
| Action and completion status | Inspection or repair, and whether the operation returned normally |
| Initial and final EPUBCheck codes | Sorted unique technical message IDs, such as `RSC-008`; no message text |
| Confirmed safe repair types | Diagnoses actually confirmed by the existing pipeline |
| Validation and repair outcomes | Original/final validity, whether a repair attempt is known, and repair success |
| Structured refusal reason | An approved technical reason code, without detailed error text |
| Fatal/error/warning counts | Validation counts where available |
| File-size category | A coarse size bucket; not the exact EPUB size |
| Processing duration | Elapsed milliseconds |
| Software versions | EPUB Doctor and EPUBCheck versions where available |

Unavailable information is recorded as null. Initial/final codes distinguish
validation before repair from revalidation; inspection has no final-validation
code list. This technical data is suitable for counting errors and outcomes,
but individual events are retained as events when telemetry is enabled; it is
not limited to pre-aggregated totals.

Application telemetry **does not contain**:

- EPUB files, book text or the contents of files inside the EPUB;
- input/output paths, filenames or internal ZIP filenames;
- book titles, authors, ISBNs, publication identifiers or publication metadata;
- full EPUBCheck messages, locations or detailed repair/exception text;
- extracted URLs, input download links or output capability URLs;
- IP addresses, ChatGPT/OpenAI user IDs or account identifiers;
- hashes or fingerprints of the EPUB.

This exclusion applies specifically to application telemetry. It does not mean
that a book's metadata is absent from the input, repaired file or returned
inspection report, or that network providers never process an IP address.

## Retention of technical telemetry and operational logs

For the supplied deployment, telemetry remains disabled and no application
telemetry retention period applies. **The current JSONL writer has no automatic
age-based deletion.** When enabled with the supplied defaults, the writer keeps
one active file and up to five archives of 10 MiB each, deleting the oldest archive
on rotation. This limits newly written logs to 60 MiB; older oversized files and
separate copies/backups require operator management. Low-volume events may remain
indefinitely unless the operator implements age-based deletion. This notice does
not promise a 30-day or any other unimplemented deletion deadline.

Before enabling telemetry on the public service, the operator must publish its
actual retention period and implement and verify the corresponding deletion
policy, including any copied logs or backups. Only then should this notice's
disabled-telemetry statement be replaced with that verified policy.

- **Future enabled-telemetry retention period (only after deletion is implemented):**

The supplied app and Nginx configuration disables HTTP access logging; Nginx
error logging is also disabled to avoid recording capability URLs. Operational
application messages and container logs are separate from application telemetry.
Docker logs have size-based rotation, which is not a guaranteed number of days.
The operator must complete the deployment-specific information below:

- **Operational log retention (period or deletion criteria):**
- **Hosting/TLS proxy logs, IP address handling and retention:**
- **Backups/snapshots, included data and deletion period:**

## Other services and recipients

ChatGPT/OpenAI already processes the file you attach and the conversation under
its own applicable policies. EPUB Doctor's temporary workspace deletion does not
delete your original ChatGPT attachment, conversation or any copy held by other
services. The source file provider also handles the download connection.

The hosting and HTTPS proxy/tunnel providers identified above handle the public
service's infrastructure and network traffic. Their processing locations,
access, logging and any international transfers must be described for the
actual deployment. No unverified claim is made that data stays exclusively in a
particular country or that all providers retain nothing.

Apart from returning the results to the requesting client and making a
successful repaired copy available through its capability link, the supplied
processing pipeline does not distribute the EPUB to an additional analysis
service. Sharing the capability URL allows others to obtain that copy.

## Privacy questions and requests

Contact the operator using the privacy contact above for questions or requests
about access, correction or deletion where applicable. Do not send an EPUB,
complete signed input URL or capability URL in an initial privacy inquiry.
Expired download links cannot retrieve a deleted output, and technical telemetry
contains no account or book identifier for searching by user or book.

For an EU/EEA deployment, the operator must also complete the applicable legal
basis, rights and complaint information, including the relevant supervisory
authority, and any required international-transfer information before
publication.

- **Applicable privacy rights and how to exercise them:**
- **Supervisory authority / complaint contact:**
- **International transfers and applicable safeguards:**
