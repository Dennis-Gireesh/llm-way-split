# Architecture

## Document status

This document defines mandatory production boundaries. The README and release notes identify runnable surfaces for each version; anything absent there is planned rather than shipped.

Version `0.1.0` provides the end-to-end browser flow and a CLI/library foundation on a single Mac or Linux host. Email ingestion, WhatsApp publishing, broad carrier-specific adapters, unattended schedules, and multi-user Internet hosting are later extensions unless a release explicitly promotes them.

## Design goals

- Start locally in a browser with minimal setup, using port `9876` rather than `8080`.
- Detect and validate suitable operator-approved local model endpoints, then let the operator choose a ready model before a statement is processed.
- Keep the core carrier-agnostic through a versioned normalized bill schema.
- Use models for extraction and classification, not for arithmetic or authority.
- Make the same normalized bill and deterministic engine drive CLI, browser, tests, and destinations.
- Make every external side effect previewable, explicitly confirmed, traceable, and recoverable where the destination permits.
- Fail closed on incomplete extraction, reconciliation mismatch, ambiguity, duplicate risk, or uncertain remote state.

## Logical components

```text
Browser / CLI
      |
      v
Ingestion -> text extraction / OCR -> local model adapter
      |                                  |
      +------------- evidence -----------+
                         |
                         v
              normalized Bill schema
                         |
                         v
          validation and confidence gates
                         |
                         v
       deterministic allocation and rounding
                         |
                         v
              reconciliation / duplicate gate
                         |
                 preview + confirmation
                         |
                         v
                 Splitwise destination

All stages record minimized run and audit state in SQLite.
```

### Operator interfaces

The web interface listens on port `9876` in the container and binds to host
loopback by default. Version `0.1.1` opens directly without a browser password;
origin and CSRF controls remain request-forgery defenses, not authentication. The
interface provides setup, model readiness, upload, extraction review,
ownership/rule configuration, allocation preview, reconciliation status,
confirmation, posting status, and audit history. Opening the app must not send a
document or create an expense.

The CLI starts the same browser service and provides model/storage diagnostics, version output, and audit verification. It has no posting command and must not become a bypass around browser confirmation or reconciliation policy.

### Ingestion and document preparation

Ingestion accepts supported PDFs and images from an operator-selected local path or upload. It computes a content fingerprint before processing, assigns an internal identifier, and never treats the supplied filename as a trusted path.

For PDFs, the system first attempts local text extraction. It invokes local rasterization and OCR only for pages or documents that lack usable text. Extraction preserves page/evidence references where possible. Resource limits and format checks apply before model invocation.

Email attachment ingestion is a future adapter. It must end at the same ingestion boundary and may not post automatically merely because a sender or subject matches a rule.

### Model discovery and extraction

The model layer supports Ollama and explicitly configured OpenAI-compatible endpoints. Compatibility describes an API shape, not where processing occurs. The setup flow must show the endpoint host and whether it is considered local before any document content is sent.

Discovery is limited to configured loopback/container-local endpoints and does not scan arbitrary local networks. A statement-free readiness check verifies reachability, strict structured output, and deterministic reconciliation while reporting Ollama capability metadata when available. The operator chooses a model. Each run stores a WaySplit attestation digest over endpoint, provider, model name, and the provider-reported model digest (or an empty marker when unavailable). The same statement-free probe is repeated immediately before document extraction; a changed identity or readiness failure aborts the run. This narrows but cannot eliminate the interval in which an independently administered model service could replace a model.

The model receives document evidence and a strict versioned output contract. It may extract identities, billing periods, totals, line items, charge categories, scope/ownership hints, and confidence/evidence references. It has no credentials or tool access and cannot post, execute code, select recipients, reconcile money, or decide that a bill is safe.

### Normalized bill contract

The `Bill` document is the carrier-neutral boundary between probabilistic extraction and deterministic processing. The versioned schema represents:

- carrier label as descriptive metadata, not a dispatch requirement;
- account/bill identity suitable for duplicate checks, with sensitive display values minimized;
- statement date, service period, due date when present, and currency;
- independently identified statement total;
- account-level and line-level charges with stable internal IDs;
- signed monetary amount, category, description, scope, and optional service-line/device reference;
- discounts, credits, taxes, fees, installments, and one-time charges without erasing their sign or type;
- extraction confidence and evidence references; and
- its normalized schema version.

The surrounding persisted run envelope—not model output—records the source content fingerprint, source metadata, selected endpoint/provider/model, and the WaySplit model-attestation digest described above. Provider metadata remains visible in the readiness result; the persisted digest must not be represented as the provider's native artifact digest. Prompt and application behavior are versioned with the installed release.

The persisted representation uses decimal strings or integer minor units according to the schema contract. Binary floating-point values are not accepted for monetary decisions.

Carrier adapters are optional pre/post-processors for format detection, ingestion hints, vocabulary normalization, known-total validation, and documented edge cases. Every adapter emits the same normalized schema. An adapter cannot perform final allocation, weaken validation, or override reconciliation.

### Deterministic policy engine

The policy engine takes a validated normalized bill plus versioned household configuration. Rules map stable service-line/device/account identifiers to participants and select an allocation policy for shared charges. Rule priority and conflict behavior are explicit.

The engine:

1. resolves each charge to an owner or shared allocation rule;
2. rejects missing or conflicting mappings;
3. applies weights or fixed shares using exact decimal arithmetic;
4. rounds once at the documented boundary;
5. distributes indivisible minor-unit remainders with a stable, reviewable ordering; and
6. emits a per-charge explanation and participant totals.

The model may suggest classifications or owners, but those suggestions remain reviewable inputs. It never supplies final participant totals.

### Validation, reconciliation, and posting eligibility

Validation is layered:

- schema validation rejects malformed or unsupported model output;
- semantic validation checks dates, currency, signs, references, duplicates, and required evidence;
- confidence gates require review of uncertain fields;
- bill reconciliation compares the sum of normalized charges to the independently extracted statement total;
- allocation reconciliation proves the participant amounts sum to the allocatable normalized amount; and
- duplicate/posting checks inspect content fingerprints, bill identity, destination, prior intents, and remote IDs.

A failed bill-total reconciliation is an unconditional posting block. Posting requires exact equality at the cent boundary; a model cannot infer, waive, or offset a difference.

Reconciliation is necessary but insufficient: compensating extraction errors can still sum to the printed total. Evidence, confidence, classification, ownership, and recipient review remain visible.

### Preview and confirmation

The preview contains source identity, dates, currency, statement and normalized totals, reconciliation difference, every charge and its evidence/confidence, allocation rules applied, participant mappings, final monetary fields, and duplicate status. A destination adapter may add a non-financial correlation reference; it may not alter confirmed money, participants, currency, date, description, or allocation fields.

Confirmation is bound to a digest of the normalized bill, rule set, participants, destination, and payload. Changing any of them invalidates the confirmation. Posting re-runs all deterministic gates immediately before creating the remote intent.

### Destinations

The first destination is Splitwise through its official API. The adapter accepts only a confirmed immutable plan and minimum required credentials. It does not receive the original statement or unrestricted model output. It records intent, outcome, response classification, and remote expense ID without logging secrets.

Posting uses a durable state machine such as `previewed`, `confirmed`, `posting`, `posted`, `failed`, and `indeterminate`. A timeout after request transmission is indeterminate, not a safe automatic retry. Where Splitwise does not provide a usable idempotency mechanism, the adapter must check remote state or require operator resolution before retrying.

WhatsApp is a future destination because account setup, templates, recipient consent, webhooks, and delivery semantics add operational and privacy complexity. It must receive a separately reviewed minimal summary and cannot be enabled as an implicit fallback from Splitwise.

### Persistence and audit

SQLite is the single-host system of record for statement fingerprints, normalized runs, household snapshots, validation results, allocation plans, confirmations, posting state, remote identifiers, and minimized audit events. Original statements and full OCR/model request/response envelopes are not persisted by default. Version `0.1.0` offers an explicit source-retention switch without automatic expiry; operators enabling it must manage deletion themselves.

State transitions that affect posting are transactional. Audit events are append-oriented and hash-linked. The chain helps detect corruption and basic alteration but is not immutable against an attacker who controls the database and application. A release must not market it as an immutable ledger.

Schema migrations are ordered and versioned. Releases must declare forward/backward compatibility, back up before migration, and provide the rollback procedure described in `RELEASES_AND_ROLLBACK.md`.

## Deployment topology

The supported baseline is Docker Compose on one operator-controlled Mac or Linux host:

- the application exposes host port `9876`;
- a persistent data volume holds SQLite and application state;
- an input path, when mounted, is read-only;
- secrets are injected at run time and excluded from images and Git;
- Ollama may run as an optional Compose service or as an external local service; and
- only the application talks to Splitwise, and only during preflight/post/verification actions initiated by the operator.

The Compose network does not itself make an endpoint private. Operators must review host bindings and firewall rules. Public hosting and multi-node databases are outside the baseline architecture.

## Configuration hierarchy

Configuration separates non-secret behavior from secrets:

- checked-in example configuration contains safe placeholders only;
- operator configuration selects endpoints, model defaults, retention, safety thresholds, household rules, and destination settings;
- secret values come from an excluded runtime source; and
- each preview stores the household snapshot and a digest of the exact deterministic expense plan.

Configuration validation fails before document processing or posting when a required value is missing or unsafe. No default participant, group, or ownership mapping is invented.

## Observability and failure behavior

Logs use run IDs and state transitions, not raw document text, phone numbers, tokens, prompts, or full model responses. User-visible errors explain which gate failed and what can be corrected without exposing sensitive internals.

The service resumes safely after a crash by reading durable state. It never infers that an interrupted external request failed merely because a success response is absent. The service health check covers application, database, and audit integrity; model readiness and optional destination connectivity are checked separately in the browser. Destination unavailability does not prevent local dry-run work.

## Extension rules

New ingestion methods, models, carrier adapters, rule types, and destinations plug into typed boundaries rather than branching the core by carrier. Every extension must declare:

- data received and emitted;
- network and credential requirements;
- deterministic validation and failure behavior;
- privacy/retention impact;
- idempotency and rollback behavior for side effects; and
- synthetic tests, provenance, and dependency licenses.
