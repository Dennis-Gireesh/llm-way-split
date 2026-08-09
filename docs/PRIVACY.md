# Privacy

## Summary

`llm-way-split` is designed to process mobile statements on an operator-controlled machine by default. It is self-hosted software, not a hosted privacy service. The project maintainers do not receive an operator's statements merely because the software is installed.

Actual privacy depends on deployment and configuration. A remote model endpoint, a network-exposed interface, Splitwise posting, future WhatsApp support, backups, or third-party monitoring can move data outside the local host. The application must disclose those boundaries before use.

This document describes the intended production data policy. Release documentation must identify deviations or unfinished controls. It is not legal advice or a substitute for an operator's obligations to household members.

## Data categories

The application may process:

- statement files and page images;
- names, phone numbers, addresses, carrier/account identifiers, service lines, device information, and billing dates;
- charges, taxes, fees, credits, installments, totals, and payment-related descriptions;
- extracted text, model input/output, confidence, and source evidence;
- household participant and ownership rules;
- Splitwise user/group identifiers, expense payloads, and remote expense IDs;
- model endpoint and model identifiers;
- document fingerprints, plan/configuration digests, run state, errors, and audit events; and
- credentials supplied by the operator.

Even a hash or masked identifier may be personal data when it can be linked to a household or other records.

## Default data flow

1. The operator selects or uploads a statement to the self-hosted application.
2. Local parsers and OCR derive text or page images.
3. The application sends required evidence to the selected model endpoint.
4. A normalized bill, validation result, deterministic allocation, and preview are produced locally.
5. Nothing is posted externally until the operator confirms the exact plan.
6. On confirmation, only the destination payload is sent to Splitwise. The original statement is not part of that payload.

An Ollama or OpenAI-compatible endpoint is local only when it runs on an operator-controlled local address. Version `0.1.0` rejects remote-model mode rather than offering a cloud opt-in. The term "OpenAI-compatible" describes a local API shape and must not be presented as a privacy claim. A future remote option would disclose submitted text/images to that provider and requires a separate UI consent and privacy review.

## Storage and retention

Version `0.1.0` minimizes persistence as follows:

- original uploads, rasterized pages, OCR text, and raw model exchanges are temporary and removed after the run reaches a durable safe state;
- SQLite retains document fingerprints, normalized bill/allocation state needed for explanation and duplicate detection, confirmations, posting outcomes, remote IDs, and minimized audit events;
- logs retain identifiers and state changes, not raw document content or credentials; and
- source-file retention is off by default and must be explicitly enabled. Raw model request/response envelopes are not stored.

The source upload is deleted after extraction by default (`WAYSPLIT_RETAIN_SOURCE=false`). Normalized bills, evidence excerpts, allocations, household configuration, fingerprints, remote IDs, and audit events remain in SQLite until the operator removes the application data. Source retention, when explicitly enabled, has no automatic expiry in `0.1.0`. Operators must always treat the application data directory as sensitive statement-derived data.

Deleting a record or temporary file does not guarantee forensic erasure. Data may remain in SQLite free pages or journals, filesystem snapshots, container storage, crash dumps, browser caches, screenshots, or backups. Secure deletion depends on the host filesystem, encryption, backup policy, and operating system. Database compaction, if offered, is maintenance rather than a guarantee of erasure.

## Network disclosure

### Local model endpoint

The model receives the portions of extracted text or page images needed for structured extraction. A compromised or shared model service may retain or expose that content. Operators should isolate the endpoint, restrict access, and understand its own logging and retention.

Model discovery is limited to expected loopback endpoints and operator-approved addresses. It should not scan an arbitrary network or transmit a statement as part of discovery/readiness checks.

### Splitwise

When the operator explicitly confirms a post, Splitwise receives the information required to create the expense. Depending on the selected plan and API requirements, that can include:

- participant and group identifiers;
- date, currency, description, category, total, and participant shares; and
- an optional human-readable breakdown selected for the expense.

The original PDF/image, full OCR text, raw model response, local audit history, and unrelated account fields must not be sent. Once sent, data is governed by Splitwise and the operator's account settings. Removing local data does not delete the remote expense.

Connecting Splitwise also requires informed end-user authorization under Splitwise's current API terms. The application must present a prominent link to this privacy document, identify the data sent and purpose, and obtain explicit consent before enabling the integration. Consent to connect an account is separate from confirmation of each expense.

Splitwise may collect information about the integration's use of its API ("API Usage Data") and, under its published API terms, may use that information for any business purpose. That provider-side collection is controlled by Splitwise, not by this self-hosted application. Operators must give every affected Splitwise end user this disclosure and obtain their explicit consent before the integration processes their Splitwise materials; an operator checking a box cannot consent on another person's behalf.

Splitwise's published self-serve API terms state that the API is not intended for commercial or fee-based services and that access may be subject to rate limits or a Splitwise Pro requirement. An operator or distributor must review the current terms before enabling the integration and obtain any additional permission required for their use case. This project does not represent that every hosted, commercial, or fee-based deployment is permitted. This summary is informational, not legal advice.

### WhatsApp and other future destinations

WhatsApp support is not part of the baseline privacy surface. Before release, its adapter must document recipient consent, message/template contents, Meta/processor involvement, credentials, webhook data, delivery records, retention, and deletion limitations. No destination may be silently enabled or used as an automatic fallback.

### Project services and telemetry

The project requires no maintainer-operated service for ordinary local processing. Version `0.1.0` sends no analytics or crash reports. If opt-in diagnostics are added, the consent screen and release privacy notes must enumerate fields, destination, retention, and a way to withdraw; document-derived content and credentials remain prohibited.

Container registries, package indexes, operating-system updates, model downloads, and vulnerability feeds may observe IP addresses and requested artifact names during installation or update. These are deployment supply-chain interactions, not statement processing, but operators of restricted networks should account for them.

## Credentials

Splitwise tokens and any future destination credentials are secrets. In `0.1.0`, a Splitwise token can be supplied only through the unlocked browser for the current tab/request; server-side Splitwise credentials are deliberately unsupported so an untrusted parser/model peer cannot drive a destination side effect. Tokens must be excluded from Git and images, hidden from later UI responses, and redacted from logs/errors. The project should prefer narrow scopes when providers make them available.

The browser-unlock secret is separate from destination credentials. When it is generated automatically, it is printed once to the local terminal/container log so the operator can unlock the browser. Treat that startup output as sensitive, restrict log access, and restart WaySplit to rotate the generated value. The secret is never placed in a URL; a successful unlock exchanges it for an eight-hour HttpOnly session cookie.

If a credential is exposed, revoke or rotate it at the provider, review remote activity, replace the local value, and treat logs/backups containing it as compromised. Encryption at rest may reduce exposure from a stolen disk but does not protect a secret while the running application is using it.

## Logs and audit records

Logs must avoid:

- full names, phone numbers, postal/email addresses, and account numbers;
- statement descriptions when a category and internal charge ID suffice;
- document bytes, OCR text, prompts, model responses, or page images;
- access tokens, authorization headers, cookies, webhook signatures, and full remote responses; and
- full local paths that reveal operator identity unnecessarily.

Audit records should answer which version processed a fingerprint, which rules and plan were confirmed, which gates passed, and what remote identifier/result followed. Hashes and stable pseudonymous IDs should be used where operationally sufficient. Audit minimization can limit later debugging; diagnostic collection must be an explicit operator choice.

## Operator responsibilities

Operators should:

- tell affected household members how their billing data is processed and obtain any required consent;
- use full-disk encryption, a patched host, a non-privileged service account, and restricted filesystem permissions;
- bind the app and model to loopback unless an authenticated, encrypted network deployment is deliberately configured;
- protect and test backups, then expire them according to household policy;
- review model endpoint location and retention before processing a statement;
- review every preview, especially ambiguous ownership, before posting;
- review current destination terms, obtain required end-user consent, and make this privacy policy prominent wherever the integration is offered;
- remove browser downloads, screenshots, and exported diagnostics when no longer needed; and
- follow provider procedures to delete or correct data already sent to Splitwise or another destination.

## Contributions, issues, and support

Do not submit a real or merely redacted statement to the repository, an issue, a pull request, CI, or a maintainer. PDF redaction can leave hidden text, metadata, revision history, or attachments. Reproduce problems with synthetic data. If a vulnerability requires private handling, follow `SECURITY.md` and still minimize shared data.

## Data-subject requests

Because deployments are self-hosted, the operator—not the open-source maintainers—normally controls the application data and external destination account. Requests to inspect, correct, export, or delete household data should be directed to that operator and, for remote data, to the relevant provider. The project cannot delete data from a machine or account it does not control.
