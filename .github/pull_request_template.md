## Summary

Describe the user-visible outcome and the trust boundary affected.

## Verification

- [ ] I added or updated tests for the changed behavior.
- [ ] `make check test` passes locally.
- [ ] I used only synthetic or redacted-by-construction fixtures.
- [ ] I did not commit statements, credentials, personal identifiers, generated local data, or hidden document metadata.
- [ ] I documented dependency, model, fixture, and copied-code provenance; all included material has a compatible license.
- [ ] LLM/VLM output remains untrusted input; allocation, rounding, reconciliation, duplicate detection, and posting decisions remain deterministic.
- [ ] Reconciliation failure blocks posting, and every external side effect still requires a reviewed preview and explicit confirmation.
- [ ] I updated the threat model, privacy notes, configuration, and rollback guidance when the change affects them.

## Side effects and rollback

List external calls, stored-data changes, migrations, and the tested rollback or recovery path. Write “none” where applicable.
