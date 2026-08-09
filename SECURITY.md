# Security Policy

`llm-way-split` processes documents that can contain phone numbers, names, addresses, account identifiers, and financial information. It also may hold credentials capable of creating expenses in an external account. Treat the application and its data directory as sensitive.

This policy describes the project's intended security process. It is not a warranty that the software is free of vulnerabilities.

## Supported versions

Until the first stable release, the repository is development software and no version receives production security support.

After a stable release, the latest stable release line will receive security fixes. Older lines are supported only when the release notes explicitly say so.

| Version | Security fixes |
| --- | --- |
| Unreleased development branch | Best effort; not production-supported |
| Latest stable release | Yes |
| Older releases | No, unless announced |

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability and do not attach a real statement, database, access token, log containing personal data, or exploit that exposes another person's data.

Use the repository's **Security** tab and choose **Report a vulnerability** to open a private security advisory. If private vulnerability reporting is unavailable, contact a maintainer through a private contact method listed on that maintainer's GitHub profile. If no private channel is available, open a content-free issue asking the maintainers to enable private reporting; include no vulnerability details in that issue.

Include, when possible:

- the affected version, commit, deployment mode, and operating system;
- the minimum steps needed to reproduce the issue with synthetic data;
- expected and observed behavior;
- likely impact and required attacker access;
- suggested remediation, if known; and
- whether any credential or personal data may have been exposed.

Never send production credentials or an unredacted bill. Replace personal data with synthetic values and revoke any credential disclosed accidentally.

## What to expect

Maintainers will make a best-effort acknowledgement within seven calendar days. Triage, remediation, and disclosure timing depend on severity and maintainer availability; these are targets, not a service-level agreement. The maintainers may ask for validation against a candidate fix. Credit is offered with the reporter's consent.

For a confirmed vulnerability, the project aims to:

1. establish affected versions and practical impact;
2. prepare and test a fix without exposing unnecessary details;
3. publish a patched release and a security advisory;
4. document credential rotation, data review, or rollback actions when relevant; and
5. update the threat model or regression tests.

The project will not request that a reporter access data they do not own or disrupt a service.

## Security boundaries operators must understand

- **Local-first is a default, not an isolation guarantee.** Statement content stays on the operator-controlled host only when the configured OCR/model endpoint is local and no external destination is invoked. A remote OpenAI-compatible endpoint receives the content sent to it.
- **Splitwise is an explicit external boundary.** A confirmed post sends expense data and participant identifiers to Splitwise. Raw statements should not be sent.
- **Authorization has two layers.** The operator must explicitly consent to connecting the Splitwise account under the provider's current API/privacy terms, and must separately confirm each exact expense plan. Application confirmation does not replace provider authorization or household consent.
- **Model output is untrusted.** Extraction confidence does not make a monetary result authoritative. Deterministic schema checks, allocation, exact decimal arithmetic, duplicate detection, and reconciliation must pass before posting is even offered.
- **Reconciliation is a hard gate.** A mismatch between the statement total and normalized charges must prevent posting. Operators should review low-confidence or ambiguous ownership results even when totals match.
- **Host access defeats many controls.** A user who can read the application data directory may be able to read normalized statement data and posting credentials. Filesystem permissions, disk encryption, backups, container access, and operating-system patching remain operator responsibilities.
- **Audit history is evidence, not an immutable ledger.** Hash chaining can reveal accidental damage or unsophisticated edits, but an attacker with write access to the database and application can rewrite a chain unless records are anchored in a separately controlled system.

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for assets, trust boundaries, abuse cases, and required controls.

## Deployment hardening

- Bind the web service to loopback by default. If exposing port `9876` to a network, place it behind authenticated TLS termination and restrict network access.
- Do not expose Ollama or another model endpoint to an untrusted network. Use endpoint authentication when supported.
- Store secrets outside Git and outside container images. Restrict access to configuration and data volumes.
- Use pinned release artifacts and review their SBOM and vulnerability scan results before upgrading.
- Back up the SQLite database before upgrades and protect backups to the same standard as live data.
- Keep statement input directories out of the repository. Prefer read-only input mounts and a dedicated, non-privileged service account.
- Rotate Splitwise credentials after suspected compromise, then inspect remote expenses for unauthorized or duplicate activity.
- Review current Splitwise API eligibility, consent, privacy-policy, account-tier, and rate-limit requirements before enabling the destination. The self-serve API must not be assumed to permit commercial or fee-based operation.

## Out of scope for private vulnerability handling

Feature requests, extraction-quality problems without a security impact, unsupported deployments, and findings produced only by an automated scanner without a reproducible impact may be handled as ordinary issues. Do not include sensitive data in those reports.
