# Threat Model

## Status and scope

This document defines security requirements for the intended production architecture. It does not claim that every control is implemented in every branch or release. Release notes and verification evidence must identify which surfaces are shipped. Until a stable release is published, treat the application as alpha software. In particular, the document-processing subprocess is resource isolation, not a containment sandbox for arbitrary parser code execution.

The scope is a single-operator or household deployment on an operator-controlled Mac or Linux host, normally through Docker Compose. The browser UI is expected on port `9876`. The application accepts statement PDFs or images, extracts a carrier-neutral bill through local text/OCR and a local model endpoint, computes a deterministic allocation, and can create a Splitwise expense only after preview and confirmation.

Email ingestion, WhatsApp publishing, remote model providers, and multi-user Internet exposure are future or optional surfaces. Enabling one expands the threat model and requires a separate review before it can be described as production-ready.

## Security objectives

The system must:

1. keep statement content on operator-controlled systems by default;
2. prevent untrusted documents and model output from controlling code execution or posting decisions;
3. compute money, allocation, rounding, and reconciliation deterministically;
4. prevent posting when totals do not reconcile or ownership/recipient mapping is unresolved;
5. require an informed, explicit confirmation for the exact posting plan;
6. detect duplicate processing and reduce duplicate external side effects;
7. minimize credentials and personal data in files, logs, containers, and outbound requests;
8. leave a useful, minimally sensitive audit trail; and
9. support safe upgrade, backup, incident response, and rollback.

Availability against a fully compromised host, malicious administrator, or destructive local user is not a primary objective. Confidentiality from an operator who controls the host is not possible.

## Assets

| Asset | Why it matters |
| --- | --- |
| Original statement | May contain names, phone numbers, addresses, account identifiers, billing history, and financial data |
| Extracted text and page images | Usually contain the same sensitive content in easier-to-search form |
| Normalized bill and allocation plan | Reveal household membership, ownership, amounts, and billing relationships |
| Splitwise token and identifiers | May permit expense creation or expose group/user information according to granted scope |
| Configuration and ownership rules | Reveal personal relationships and determine who is charged |
| SQLite database and backups | Hold fingerprints, processing state, normalized data, confirmations, remote IDs, and audit records |
| Model endpoint | Receives statement content and may expose other locally processed data if compromised |
| Release artifacts and dependency graph | Can execute with access to all of the above |

## Actors and assumptions

- **Operator:** controls the host, configuration, model selection, participant mapping, and confirmation. The operator is trusted to review previews and protect credentials.
- **Household participant:** may see shared expense information but is not assumed to have host access.
- **Remote destination:** Splitwise is trusted only to process the explicitly confirmed payload under its own policy. Its response can fail, time out, or be ambiguous.
- **Document sender/carrier:** statement layout and content are untrusted even when the carrier is legitimate. A supplied file may be malformed or malicious.
- **Model/OCR runtime:** may make arbitrary extraction errors. A locally hosted process reduces disclosure but is not inherently trustworthy.
- **Attacker:** may send a crafted document, reach an exposed web/model port, compromise a dependency or image, steal a backup, or obtain local unprivileged access. A root-level host attacker is assumed able to defeat application-level controls.

## Trust boundaries and data flow

1. **Browser to application (`9876`).** The browser sends configuration choices, documents, rules, and confirmations. Network exposure beyond loopback requires authentication, TLS, origin controls, request-size limits, and rate limiting.
2. **Application to document tooling.** PDF parsers, rasterizers, and OCR process attacker-controlled bytes in a disposable subprocess. The child has cumulative resource budgets, wall/CPU limits, bounded non-executable IPC, a scrubbed environment, and closed unrelated descriptors. It still shares the application's OS identity, accessible filesystem, and network; this boundary limits faults but does not contain a native-code exploit.
3. **Application to model endpoint.** Text, images, and a constrained extraction request cross into Ollama or another configured OpenAI-compatible service. Locality must be determined from configuration and shown to the operator; protocol compatibility does not imply privacy.
4. **Model output to deterministic core.** Structured output is untrusted. Schema validation, semantic checks, currency rules, allocation, exact rounding, and reconciliation form a hard boundary.
5. **Application to SQLite and local files.** Sensitive derived data and credentials may reach persistent storage, journals, temporary files, backups, or logs.
6. **Confirmed plan to Splitwise.** Only a minimal, validated expense payload may leave the host. A response may be successful, failed, or indeterminate.

The model has no direct access to Splitwise credentials, the database, the shell, network tools, or posting functions. Text found in a statement is data, never an instruction to the application.

## Primary threats and required mitigations

### Sensitive-data disclosure

Threats include accidental cloud model configuration, a service bound to all interfaces, verbose logs, retained page images, world-readable volumes, core dumps, screenshots, issue attachments, backups, or outbound telemetry.

Required controls:

- default to a loopback/local model endpoint and clearly label any non-local endpoint before content is sent;
- make all cloud processing opt-in and describe exactly what data leaves the host;
- bind the application to loopback by default and document authenticated TLS termination for network use;
- never log original document bytes, full OCR text, model prompts/responses, access tokens, or unnecessary personal fields;
- use restrictive permissions on data, configuration, temporary storage, and backups;
- define and implement retention/deletion behavior, including temporary files;
- ship no telemetry by default; any future telemetry must be opt-in and exclude document-derived data; and
- keep statements, databases, `.env` files, and credentials outside Git and build contexts.

Limit: filesystem permissions do not protect against a privileged host user. File deletion does not erase copies in SQLite journals, snapshots, container layers, or backups.

### Malicious or pathological documents

Threats include parser vulnerabilities, embedded content, oversized files, decompression bombs, extreme page counts or dimensions, path traversal in filenames, and resource exhaustion.

Required controls:

- accept only explicitly supported formats and verify content independently of the filename;
- assign server-generated identifiers and never use an uploaded filename as a filesystem path;
- cap upload size, page count, raster dimensions, processing time, memory, and parallelism;
- process documents as a non-root user, minimize mounted data and host-device access, and never describe same-UID subprocess isolation as a filesystem sandbox;
- keep parsing libraries pinned and scanned, and update quickly for relevant advisories;
- discard or quarantine unsupported embedded content; and
- return a safe failure without posting when extraction is incomplete or tooling crashes.

Implemented subprocess controls in this alpha include a `spawn`-based worker,
page/text/image/pixel budgets, bounded UTF-8 JSON IPC (no parent-side unpickling),
a 180-second wall timeout, a 120-second CPU limit, and a 1 GiB Linux address-space
limit. Before parsing, the worker removes application/API credentials and proxy
variables, keeps only a minimal locale/Tesseract execution environment, closes
unrelated inherited descriptors, disables core dumps, and applies Linux
`PR_SET_NO_NEW_PRIVS` and non-dumpable process state. macOS does not reliably
enforce the requested per-process memory rlimits, so memory limiting there is
best effort; the cumulative document budgets and disposable process remain active.
Pytesseract may materialize one OCR result inside that worker before the cumulative
text limit rejects it; OCR output is not yet streamed, but oversized text cannot
cross the bounded IPC boundary or enter the returned `DocumentContent`.

**Residual alpha boundary:** the worker is not a seccomp/Seatbelt sandbox, does
not have a separate UID, mount namespace, or network namespace, and can still use
the permissions of the WaySplit service if parser-native code is compromised. In
Docker it can read the same `/data` volume and reach the same container network;
on a native installation it can potentially reach any file or local service the
operator account can reach. Environment scrubbing prevents ordinary inherited
model/Splitwise credentials from being present, but it cannot guarantee
confidentiality from arbitrary same-UID code (for example, process inspection or
reading other accessible secret files). Do not expose statement upload to
anonymous, multi-user, or adversarial senders in this release.

### Prompt injection and model confusion

A statement can contain text that tells a model to ignore its schema, invent charges, expose secrets, or invoke a tool. Benign layouts can also lead to hallucinated or duplicated lines.

Required controls:

- give the model no tools, credentials, database access, or posting capability;
- use strict, versioned structured output with bounded fields and reject additional or malformed data;
- keep extraction/classification separate from deterministic decision logic;
- retain evidence references such as page and region or source text spans where practical;
- flag low-confidence, conflicting, missing, or ambiguous values for review;
- reconcile normalized charge totals to an independently extracted statement total; and
- ensure every failure path disables confirmation and posting.

Reconciliation proves arithmetic consistency only. It does not prove that a model assigned a charge to the correct person, categorized it correctly, or extracted two compensating errors. The preview must expose source evidence and ownership decisions.

### Monetary error and recipient confusion

Threats include binary floating-point error, inconsistent rounding, discounts applied twice, wrong owners, stale rules, homonymous participants, currency mismatch, and carrier-specific assumptions leaking into the core.

Required controls:

- use decimal arithmetic and explicit currencies;
- define a deterministic smallest-unit rounding and remainder algorithm;
- preserve charge signs and distinguish fees, taxes, credits, installments, and account-level items;
- require stable participant identifiers, not display names alone, for remote posting;
- version the carrier-neutral schema and allocation rules used for each run;
- treat carrier adapters as hints/validation layers that cannot bypass core gates;
- show per-charge allocation and reconciliation differences in the preview; and
- bind confirmation to a digest of the normalized bill, rules, participants, destination, and final amounts. Any change invalidates confirmation.

### Duplicate, replayed, or ambiguous posting

Threats include processing the same statement twice, repeated clicks, concurrent workers, timeout after a successful remote write, retry without idempotency, and restoring an old database that lacks a recent remote ID.

Required controls:

- fingerprint original content and a stable bill identity, while allowing the operator to inspect legitimate corrected statements;
- persist a posting state machine and serialize posting for a bill/destination pair;
- reserve the intent atomically before the network request;
- use a destination idempotency facility when available and record the key;
- record the exact confirmed-plan digest and remote expense identifier;
- verify the remote result where the API permits; and
- treat a timeout or contradictory response as **indeterminate**. Do not retry automatically until remote state is checked.

Rollback of application code does not reverse a Splitwise expense. External reversal is a separate, explicit action and must be audited.

### Authentication, request forgery, and exposed services

The browser interface is intended for an operator-controlled host. Version `0.1.0`
requires an out-of-band browser unlock token, exchanges it for a bounded HttpOnly
session, and retains separate origin/CSRF checks. That barrier keeps an untrusted
local model or parser process from simply minting its own browser session, but it
is not a multi-user identity system, rate limiter, or substitute for TLS. If the
unlock token or session is exposed, another reachable machine could read
statements or trigger processing and confirmation flows.

Required controls for any non-loopback deployment:

- authenticated TLS termination with a strong operator identity;
- secure session and cookie settings, CSRF protection, origin validation, and clickjacking defenses;
- no state changes through safe/idempotent HTTP methods;
- re-authentication or equivalent deliberate confirmation for posting;
- strict forwarding-header and proxy trust configuration; and
- network rules that prevent public access to SQLite files, input directories, and the model endpoint.

SSRF controls must restrict model endpoints to an explicit operator-approved allowlist. Automatic model discovery must be limited to expected local endpoints and must not scan arbitrary networks or accept a document-provided URL. A configured model API key may be used for authenticated OpenAI-compatible discovery only after the endpoint is normalized and matched to that server-side allowlist; redirects and environment proxies remain disabled.

### Credential theft

Threats include secrets committed to Git, embedded in images, printed in logs, returned to the browser, exposed through process inspection, or included in diagnostics.

Required controls:

- inject secrets at runtime from files or secret mechanisms outside Git;
- use the narrowest available token scopes and separate test from production credentials;
- redact secrets in errors and diagnostics and never return them after initial entry;
- prevent secret-containing files from entering the build context;
- run secret scanning in local hooks or CI and block known-secret patterns; and
- document rotation and invalidate credentials after suspected compromise.

### Database and audit tampering

Threats include direct SQLite edits, corruption, rollback to an old backup, concurrent write failures, and deletion of evidence after an incorrect post.

Required controls:

- use transactions, foreign keys, integrity checks, and a single supported migration path;
- append audit events for extraction version, validation result, rule version, plan digest, confirmation, posting transitions, and remote IDs;
- chain audit event hashes and verify the chain during health checks and before posting;
- back up before migrations and test restoration; and
- minimize personal data in events while retaining enough evidence to explain a decision.

An unkeyed hash chain is tamper-evident only when an attacker cannot replace all later records or the verification code. Stronger assurance requires anchoring checkpoints or signatures outside the database; that is a future control, not an implied property of SQLite.

### Supply-chain compromise

Threats include malicious or abandoned dependencies, mutable container tags, compromised model files, poisoned base images, and CI credential theft.

Required controls:

- pin direct and transitive dependencies and container images; release deployments should use immutable digests;
- produce an SBOM for release artifacts and scan code, dependencies, images, and secrets in CI;
- use least-privilege CI permissions, protected release environments, and no statement fixtures containing real data;
- verify provenance/signatures or hashes for artifacts when the upstream provides them;
- document the exact model identifier and digest where available; and
- publish versioned releases with checksums, migration notes, known limitations, and rollback instructions.

## Security invariants for posting

Posting eligibility is false unless all of the following are true at the same time:

- document parsing and extraction completed without a truncation or unsupported-page error;
- the normalized bill conforms to the supported schema version;
- currency and all monetary values are valid decimals;
- the normalized total reconciles exactly to the independently identified statement total at the cent boundary;
- allocation sums exactly to the normalized amount after deterministic rounding;
- every charge and remote participant has an unambiguous mapping;
- duplicate and posting-state checks are clear;
- the preview shown to the operator matches the current plan digest;
- the operator explicitly confirmed that digest; and
- credentials and destination configuration pass preflight checks.

There is no model-confidence override for a failed invariant. Administrative or debug modes must not bypass these gates in a production build.

## Residual risks

Even with these controls:

- OCR or a model can produce a plausible, fully reconciled but semantically wrong bill;
- an operator can confirm an incorrect preview;
- carrier wording or document layout can change without notice;
- a local model process, parser, dependency, host, browser extension, or remote destination can be compromised;
- the parser subprocess can limit resource damage without containing a same-UID filesystem or network exploit;
- duplicate detection can miss semantically identical statements with materially different bytes or identities;
- Splitwise may accept a request while the local client observes a timeout; and
- backups and screenshots can outlive in-app deletion.

Production documentation and the UI must state these limitations plainly.

## Review triggers

Review and update this threat model before releasing any of the following:

- email or automatic inbox ingestion;
- WhatsApp or another destination;
- remote/cloud model support promoted beyond an expert opt-in;
- multi-user accounts, public network exposure, or Internet hosting;
- automatic posting or scheduled unattended processing;
- a new document parser with native code;
- storage of original statements or page images by default;
- externally anchored audit records; or
- a database or deployment architecture other than the documented SQLite single-host model.
