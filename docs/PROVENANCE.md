# Provenance and Third-Party Material

## Project origin

`llm-way-split` is an independent implementation of a local-first, carrier-agnostic mobile statement splitter. Its architecture was informed by public product behavior, public repository descriptions, and official integration documentation. At repository inception, no source code, prompts, tests, fixtures, documentation, visual assets, or statement samples were copied from the reference projects listed below.

The ideas that mobile bills can be extracted, allocated, previewed, and posted to a shared-expense service are used as product context. This project independently defines its normalized schema, extraction contract, deterministic monetary engine, safety gates, storage model, browser experience, tests, and documentation.

This statement records project policy and known inception provenance; it is not a legal opinion. Every contributor remains responsible for the material they submit.

## Reference projects and boundary of influence

| Reference | Publicly observed relevance | Reuse status |
| --- | --- | --- |
| `angelshila/tmobile-splitwise-automation` | T-Mobile-to-Splitwise workflow; recurring/one-time charges, ownership, weighted shares, installments, add-ons, dry run, posting, verification | Behavioral reference only. No source or expressive material copied. No declared license was identified during initial research, so redistribution/adaptation is prohibited without permission or a later verified license. |
| `chaitanya-mittapalli/tmobile-splitwise-bill-automation` | Folder-based bill ingestion, splitting, and an itemized breakdown image | Behavioral reference only; no source or assets copied. |
| `shantanuwadnerkar/tmobile_splitwise` | Browser acquisition, model extraction, and Splitwise publishing | Behavioral comparison only; its cloud-model design is not adopted as the local default. No source copied. |
| `vnallamhawk/tmobile-splitwise-sync` | Email attachment ingestion, dry run, confirmation, and Splitwise sync | Behavioral reference for possible future ingestion; no source copied. |
| `gauthamkolluru/verizon-bill-splitter` | Verizon statement-to-line-item extraction | Carrier edge-case reference only; no source or fixtures copied. |
| `Haroon96/ATT-Splitwise-Sync` | AT&T-to-Splitwise workflow | Carrier/integration landscape reference only; no source copied. |
| `pranavgupta2603/SplitwiseGPTVision` | Generic vision extraction and Splitwise integration | Behavioral reference only; no source copied even though an MIT license was observed during initial research. Any future reuse requires a fresh license check and attribution. |
| `rfdez/n8n-nodes-splitwise` | Optional workflow automation through an n8n community node | Ecosystem reference only; no source copied. |
| `nexu-io/html-anything` | Local browser-first onboarding and model-discovery experience | UX concept reference only. No code, prompts, copy, branding, layout, or assets copied. This project independently implements its setup flow. |
| Splitwise official API documentation and terms | Supported API contract and conditions for the first destination | Used as the authoritative protocol/terms reference. API facts and required field names may be implemented; documentation prose/examples are not copied beyond what license or fair-use constraints allow. The integration requires current-terms review and end-user consent. |
| Meta WhatsApp documentation | Setup, template, webhook, and delivery constraints for a possible future adapter | Planning reference only until that adapter is implemented. Documentation prose and examples are not copied. |

Repository names identify their respective owners and do not imply endorsement, affiliation, or compatibility. Carrier, Splitwise, WhatsApp, Meta, Ollama, and other names may be trademarks of their owners.

## API terms and marks

The official Splitwise self-serve API terms reviewed during project planning state that the API is not intended for commercial or fee-based services, may impose rate limits or a Splitwise Pro requirement, requires explicit end-user consent, and requires a prominent privacy policy. Data submitted through the API is governed by Splitwise's privacy terms. These conditions must be rechecked against the current official terms before each production release that enables the adapter; a commercial or fee-based distributor must not infer permission from this repository and may need separate authorization.

The project uses the Splitwise name only where reasonably necessary to describe compatibility or a destination selected by the operator. It does not copy logos, brand assets, or imply sponsorship, certification, or endorsement. The same descriptive-use rule applies to carriers and other providers.

This is a provenance and release-control summary, not legal advice. Provider terms can change independently of this repository.

## License hygiene rules

The repository's original work is offered under Apache License 2.0. That license does not relicense third-party dependencies, models, APIs, trademarks, or separately distributed assets.

Contributors must:

1. implement behavior independently unless reuse is deliberately approved;
2. verify the exact license at the exact source revision before copying or adapting any material;
3. confirm that the license is compatible with the intended distribution and use;
4. retain required copyright, license, attribution, modification, and `NOTICE` information;
5. record the source URL, revision, files/sections used, license/SPDX identifier, modifications, and review decision;
6. add third-party license text or notices to the distributed artifact when required;
7. avoid code, tests, prompts, fixtures, docs, images, or other expressive material with no license or an incompatible/unclear license; and
8. obtain explicit permission from the rightsholder when a license does not grant the needed rights.

A repository being public, searchable, source-available, or useful does not grant permission to copy it. A high-level behavior can be reimplemented without reproducing the original expression, but copying names, comments, structure, tests, prompts, examples, or distinctive UI text may still create provenance and license obligations.

If uncertain, stop and open a provenance review issue without pasting the questionable material.

## AI-assisted contributions

AI assistance does not remove contributor responsibility. Before submitting AI-generated or transformed content, the contributor must review it for:

- unexpected resemblance to a third-party implementation;
- license or attribution requirements;
- fabricated provenance or API facts;
- leaked personal data, secrets, or statement content; and
- correctness at monetary, security, and privacy boundaries.

Do not ask a model to reproduce a named repository, parser, prompt, fixture, or UI. Ask for behavior from this project's own requirements and review the resulting diff. If the model was provided third-party source material, disclose that input and its license in the pull request so maintainers can decide whether the result is acceptable.

## Dependencies, containers, and models

Tagged-release automation produces CycloneDX and SPDX SBOMs for application and container contents, while `THIRD_PARTY_NOTICES.md` provides a human-readable direct-dependency inventory. Lockfiles and inventories do not replace notices or license review. Base images, OCR engines, fonts, frontend assets, and bundled binaries all require review.

Local models are separate works obtained from their distributors. A model's availability through Ollama or an OpenAI-compatible endpoint does not establish a license for commercial use, redistribution, or generated output. Release documentation may identify tested models but must not bundle one unless its license, size, integrity, attribution, and redistribution terms have been reviewed.

API access is also governed by provider terms and operator account permissions. The Apache-2.0 license does not grant access to Splitwise or Meta services.

## Synthetic fixtures

Test statements are created from invented identities, account numbers, phone numbers reserved or clearly non-routable for examples, dates, carriers, descriptions, and amounts. A fixture based on a real bill must not be accepted merely because visible fields were blacked out: PDFs can retain hidden text, metadata, revisions, thumbnails, or embedded files.

Fixtures derived from a carrier's layout, logo, or statement template require a separate trademark/copyright review. Prefer plain project-authored layouts that test semantics without imitating protected trade dress.

## Recording future reuse

If approved third-party material is added later, update this document and the release's third-party notice inventory in the same pull request. The record should state:

- upstream name and canonical URL;
- exact commit, tag, version, or retrieval date;
- original license and copyright notice;
- material incorporated and local paths;
- modifications made;
- required attribution or source-offer obligations; and
- reviewer and review date.

Security patches copied from an upstream project are not exempt from these requirements. When urgent remediation prevents immediate public detail, maintain a private provenance record and publish the required notice with the fixed release.
