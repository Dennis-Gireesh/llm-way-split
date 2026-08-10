from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from waysplit import __version__
from waysplit.destinations.splitwise import (
    AmbiguousDestinationError,
    SplitwiseClient,
    build_create_payload,
)
from waysplit.domain.allocation import UnresolvedOwnerError, allocate_bill
from waysplit.domain.canonical import canonical_json
from waysplit.domain.fingerprint import bill_fingerprint
from waysplit.domain.gates import (
    PostingGateConfig,
    PostingStatus,
    evaluate_posting_gate,
)
from waysplit.domain.models import NormalizedBill
from waysplit.errors import (
    ConfirmationError,
    DestinationError,
    DuplicateStatementError,
    PostingBlockedError,
    WaySplitError,
)
from waysplit.household import ExpensePreview, HouseholdConfig, build_expense_preview
from waysplit.ingest_worker import extract_document_isolated
from waysplit.model_gateway import (
    ModelGateway,
    ModelProvider,
    probe_model,
    readiness_attestation_digest,
    require_configured_endpoint,
)
from waysplit.repository import (
    PostingRecord,
    Repository,
    RunRecord,
    posting_correlation_id,
)
from waysplit.settings import Settings

LOGGER = logging.getLogger(__name__)


def _collapse_line_table_totals(bill: NormalizedBill) -> NormalizedBill:
    """Prefer one printed row-total per service when models expand every column."""

    groups: dict[str, list[Any]] = {}
    for charge in bill.charges:
        if charge.scope.value == "line" and charge.service_identifier:
            groups.setdefault(charge.service_identifier, []).append(charge)
    if not groups or any(
        len([charge for charge in charges if "total" in charge.description.lower()]) != 1
        for charges in groups.values()
    ):
        return bill
    selected = [
        charge
        for charges in groups.values()
        for charge in charges
        if "total" in charge.description.lower()
    ]
    if sum(charge.amount for charge in selected) != bill.totals.current_charges:
        return bill
    return bill.model_copy(update={"charges": tuple(selected)})


def _repair_printed_summary_totals(
    bill: NormalizedBill, document_text: str
) -> tuple[NormalizedBill, tuple[str, ...]]:
    """Keep prior-cycle payments from reducing a separately printed current bill."""

    def printed_amount(label: str) -> Decimal | None:
        match = re.search(rf"{label}[^$\d-]*\$?([\d,]+\.\d{{2}})", document_text, re.IGNORECASE)
        return Decimal(match.group(1).replace(",", "")) if match else None

    services_total = printed_amount(r"Total\s+services")
    amount_due = printed_amount(r"Total\s+due")
    if (
        services_total is None
        or amount_due is None
        or services_total != bill.totals.current_charges
        or amount_due != services_total
        or bill.totals.balance_forward != Decimal("0.00")
        or bill.totals.payments_and_credits >= Decimal("0.00")
    ):
        return bill, ()
    totals = bill.totals.model_copy(
        update={"payments_and_credits": Decimal("0.00"), "amount_due": amount_due}
    )
    return bill.model_copy(update={"totals": totals}), (
        "The printed Total services and Total due were used; a prior-cycle payment "
        "was not treated as a current credit.",
    )


def _ignore_prior_cycle_payment(bill: NormalizedBill) -> NormalizedBill:
    """Correct an AT&T-style account summary that lists last month's payment.

    If a statement has no carried balance and its printed amount due already equals
    the current charges, a negative payment belongs to the prior bill. Including it
    again makes the current statement equation fail by exactly that old payment.
    """

    totals = bill.totals
    if (
        totals.balance_forward != Decimal("0.00")
        or totals.current_charges != totals.amount_due
        or totals.payments_and_credits >= Decimal("0.00")
    ):
        return bill
    return bill.model_copy(
        update={"totals": totals.model_copy(update={"payments_and_credits": Decimal("0.00")})}
    )


class WaySplitService:
    def __init__(self, *, settings: Settings, repository: Repository) -> None:
        self.settings = settings
        self.repository = repository
        self._processing_lock = asyncio.Lock()

    @property
    def gate_config(self) -> PostingGateConfig:
        return PostingGateConfig(
            reconciliation_tolerance=self.settings.reconciliation_tolerance,
            minimum_charge_confidence=self.settings.minimum_extraction_confidence,
            require_charge_evidence=self.settings.require_charge_evidence,
        )

    async def process_run(self, run_id: str) -> None:
        """Serialize extraction jobs to protect a local model from accidental overload."""

        async with self._processing_lock:
            await self._process_run(run_id)

    async def _process_run(self, run_id: str) -> None:
        run = self.repository.get_run(run_id)
        if not run.source_path:
            self.repository.fail_run(
                run_id,
                code="source_missing",
                message="The temporary source document is unavailable.",
                clear_source=True,
            )
            return
        source_path = Path(run.source_path)
        try:
            self.repository.set_extracting(run_id)
            configured_endpoint = require_configured_endpoint(
                run.model_endpoint,
                configured_endpoints=self.settings.model_endpoints,
                allow_remote=self.settings.allow_remote_model_endpoints,
            )
            api_key = (
                self.settings.model_api_key.get_secret_value()
                if self.settings.model_api_key
                else None
            )
            readiness = await probe_model(
                endpoint=configured_endpoint,
                provider=ModelProvider(run.model_provider),
                model=run.model_name,
                allow_remote=self.settings.allow_remote_model_endpoints,
                timeout_seconds=self.settings.model_timeout_seconds,
                api_key=api_key,
            )
            if (
                not readiness.ready
                or not run.model_digest
                or readiness_attestation_digest(readiness) != run.model_digest
            ):
                raise PostingBlockedError(
                    "The selected model changed or no longer passes readiness. "
                    "Select and test it again before re-uploading."
                )
            document = await asyncio.to_thread(
                extract_document_isolated,
                source_path,
                max_pages=self.settings.max_pages,
            )
            gateway = ModelGateway(
                endpoint=configured_endpoint,
                provider=ModelProvider(run.model_provider),
                model=run.model_name,
                allow_remote=self.settings.allow_remote_model_endpoints,
                timeout_seconds=self.settings.model_timeout_seconds,
                api_key=api_key,
            )
            bill = await gateway.extract_bill(document)
            bill = _collapse_line_table_totals(bill)
            bill, summary_warnings = _repair_printed_summary_totals(bill, document.text)
            decision = evaluate_posting_gate(bill, config=self.gate_config)
            self.repository.complete_extraction(
                run_id,
                bill=bill,
                logical_fingerprint=bill_fingerprint(bill),
                reconciliation=decision.reconciliation.model_dump(mode="json"),
                gate=decision.model_dump(mode="json"),
                ingestion_warnings=(*document.warnings, *summary_warnings),
                blocked=decision.status is PostingStatus.BLOCKED,
                retain_source=self.settings.retain_source,
            )
        except DuplicateStatementError as exc:
            self.repository.fail_run(
                run_id,
                code="duplicate_statement",
                message=f"This logical statement matches run {exc.run_id}.",
                clear_source=not self.settings.retain_source,
            )
        except WaySplitError as exc:
            self.repository.fail_run(
                run_id,
                code=type(exc).__name__.removesuffix("Error").lower(),
                message=str(exc),
                clear_source=not self.settings.retain_source,
            )
        except Exception:
            LOGGER.exception("Unexpected extraction failure for run %s", run_id)
            self.repository.fail_run(
                run_id,
                code="internal_error",
                message="Extraction failed unexpectedly. Review the local logs for details.",
                clear_source=not self.settings.retain_source,
            )
        finally:
            if not self.settings.retain_source:
                await asyncio.to_thread(source_path.unlink, missing_ok=True)

    def review_bill(self, run_id: str, bill: NormalizedBill) -> RunRecord:
        decision = evaluate_posting_gate(bill, config=self.gate_config)
        self.repository.replace_bill_review(
            run_id,
            bill=bill,
            logical_fingerprint=bill_fingerprint(bill),
            reconciliation=decision.reconciliation.model_dump(mode="json"),
            gate=decision.model_dump(mode="json"),
            blocked=decision.status is PostingStatus.BLOCKED,
        )
        return self.repository.get_run(run_id)

    def create_preview(self, run_id: str, household: HouseholdConfig) -> RunRecord:
        run = self.repository.get_run(run_id)
        if run.bill is None:
            raise PostingBlockedError("Extract and review a bill before creating a preview.")
        repaired_bill = _ignore_prior_cycle_payment(run.bill)
        if repaired_bill != run.bill:
            run = self.review_bill(run_id, repaired_bill)
        assert run.bill is not None
        decision = evaluate_posting_gate(run.bill, config=self.gate_config)
        try:
            allocation = allocate_bill(run.bill, household.allocation_rules())
        except UnresolvedOwnerError:
            raise
        preview = build_expense_preview(
            bill=run.bill,
            allocation=allocation,
            household=household,
        )
        blocked = decision.status is PostingStatus.BLOCKED or bool(preview.blockers)
        gate_payload = decision.model_dump(mode="json")
        gate_payload["destination_blockers"] = list(preview.blockers)
        allocation_payload = allocation.model_dump(mode="json")
        household_payload = household.json_safe()
        preview_payload = preview.model_dump(mode="json")
        preview_digest = confirmation_plan_digest(
            run_id=run.id,
            bill=run.bill,
            allocation=allocation_payload,
            household=household_payload,
            gate=gate_payload,
            preview=preview,
        )
        self.repository.save_household(household.json_safe())
        self.repository.save_preview(
            run_id,
            allocation=allocation_payload,
            household=household_payload,
            preview=preview_payload,
            preview_digest=preview_digest,
            gate=gate_payload,
            blocked=blocked,
        )
        return self.repository.get_run(run_id)

    def issue_confirmation(self, run_id: str) -> str:
        return self.repository.issue_confirmation(run_id)

    def confirmation_target(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        if run.status != "ready":
            raise PostingBlockedError("Create a passing preview before confirming a post.")
        preview = self._validated_confirmation_plan(run)
        correlation_id = posting_correlation_id(run.id, "splitwise")
        return {
            "destination": "splitwise",
            "preview_digest": run.preview_digest,
            "bill_fingerprint": run.logical_fingerprint,
            "payload": build_create_payload(
                preview=preview,
                correlation_id=correlation_id,
            ),
        }

    async def post_to_splitwise(
        self,
        run_id: str,
        *,
        confirmation_token: str,
        access_token: str | None,
        acknowledged_preview: bool,
        accepted_destination_terms: bool,
    ) -> PostingRecord:
        if not acknowledged_preview:
            raise PostingBlockedError("Confirm that the preview matches the statement.")
        if not accepted_destination_terms:
            raise PostingBlockedError("Confirm that you accept the destination provider's terms.")
        if not self.repository.integrity_check():
            raise PostingBlockedError(
                "The local database integrity check failed. Posting is disabled."
            )
        if not self.repository.audit.verify().valid:
            raise PostingBlockedError(
                "The local audit chain is invalid. Posting is disabled until it is investigated."
            )

        run = self.repository.get_run(run_id)
        if run.status != "ready" or run.bill is None:
            raise PostingBlockedError("This run is not ready to post.")
        preview = self._validated_confirmation_plan(run)
        plan_digest = run.preview_digest
        if not plan_digest:  # defensive narrowing; validation above already requires it
            raise PostingBlockedError("The preview digest is missing.")
        decision = evaluate_posting_gate(
            run.bill,
            config=self.gate_config,
            confirmed=True,
        )
        if not decision.approved or not preview.postable:
            raise PostingBlockedError(
                "The reconciliation or destination gate is blocking this post."
            )

        token = access_token.strip() if access_token else ""
        if not token:
            raise PostingBlockedError("Enter a Splitwise access token for this post.")

        self.repository.consume_confirmation(
            run_id,
            token=confirmation_token,
            preview_digest=plan_digest,
        )
        posting = self.repository.reserve_posting(
            run_id,
            destination="splitwise",
            request_digest=plan_digest,
            consent={
                "acknowledged_action": "create_expense",
                "app_version": __version__,
                "participant_consent_asserted": True,
                "privacy_policy": "/privacy",
                "terms_url": "https://dev.splitwise.com/",
            },
        )
        client = SplitwiseClient(access_token=token)
        try:
            created = await client.create_expense(
                preview=preview,
                correlation_id=posting.correlation_id,
            )
        except AmbiguousDestinationError as exc:
            self.repository.fail_posting(
                posting.id,
                ambiguous=True,
                response_summary={"message": str(exc)},
            )
            raise
        except DestinationError as exc:
            self.repository.fail_posting(
                posting.id,
                ambiguous=False,
                response_summary={"message": str(exc)},
            )
            raise

        self.repository.complete_posting(
            posting.id,
            external_id=created.expense_id,
            verified=created.verified,
            response_summary={
                "verified": created.verified,
                "verification_issues": created.verification_issues,
            },
        )
        return self.repository.get_posting(posting.id)

    async def rollback_splitwise(
        self,
        run_id: str,
        *,
        confirmation_token: str,
        access_token: str | None,
        confirmation_phrase: str,
        acknowledged_target: bool,
    ) -> PostingRecord:
        if confirmation_phrase != "DELETE":
            raise PostingBlockedError('Type "DELETE" to confirm the rollback.')
        if not acknowledged_target:
            raise PostingBlockedError("Confirm the exact destination expense before rollback.")
        if not self.repository.integrity_check():
            raise PostingBlockedError(
                "The local database integrity check failed. Rollback is disabled."
            )
        if not self.repository.audit.verify().valid:
            raise PostingBlockedError(
                "The local audit chain is invalid. Rollback is disabled until it is investigated."
            )
        posting = self.repository.posting_for_run(run_id)
        if posting is None or posting.status not in {"posted", "posted_unverified"}:
            raise PostingBlockedError("No completed app-created expense is available to roll back.")
        if posting.external_id is None:
            raise PostingBlockedError("The destination expense ID is missing.")
        run = self.repository.get_run(run_id)
        preview = self._validated_confirmation_plan(run)
        plan_digest = run.preview_digest
        if not plan_digest:  # defensive narrowing; validation above already requires it
            raise PostingBlockedError("The recorded destination plan is incomplete.")
        self._require_audited_posting_target(posting)
        token = access_token.strip() if access_token else ""
        if not token:
            raise PostingBlockedError("Enter a Splitwise access token for this rollback.")

        client = SplitwiseClient(access_token=token)
        verification_issues = await client.verify_expense(
            expense_id=posting.external_id,
            preview=preview,
            correlation_id=posting.correlation_id,
        )
        if verification_issues:
            raise PostingBlockedError(
                "Rollback is blocked because the current Splitwise expense no longer exactly "
                "matches the app-created plan: " + "; ".join(verification_issues)
            )
        self.repository.consume_rollback_confirmation(
            run_id,
            token=confirmation_token,
            preview_digest=plan_digest,
        )
        reserved = self.repository.reserve_rollback(
            posting.id,
            request_digest=plan_digest,
        )
        try:
            await client.delete_expense(posting.external_id)
        except AmbiguousDestinationError as exc:
            self.repository.fail_rollback(
                reserved.id,
                ambiguous=True,
                response_summary={"message": str(exc), "external_id": posting.external_id},
            )
            raise
        except DestinationError as exc:
            self.repository.fail_rollback(
                reserved.id,
                ambiguous=False,
                response_summary={"message": str(exc), "external_id": posting.external_id},
            )
            raise
        self.repository.mark_rolled_back(
            reserved.id,
            response_summary={"deleted": True, "external_id": posting.external_id},
        )
        return self.repository.get_posting(reserved.id)

    def rollback_target(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        posting = self.repository.posting_for_run(run_id)
        if (
            posting is None
            or posting.status not in {"posted", "posted_unverified"}
            or posting.external_id is None
        ):
            raise PostingBlockedError("No completed app-created expense is available to roll back.")
        preview = self._validated_confirmation_plan(run)
        self._require_audited_posting_target(posting)
        return {
            "destination": posting.destination,
            "posting_status": posting.status,
            "external_id": posting.external_id,
            "correlation_id": posting.correlation_id,
            "preview_digest": run.preview_digest,
            "payload": build_create_payload(
                preview=preview,
                correlation_id=posting.correlation_id,
            ),
        }

    def issue_rollback_confirmation(self, run_id: str) -> str:
        run = self.repository.get_run(run_id)
        posting = self.repository.posting_for_run(run_id)
        if posting is not None and posting.status in {
            "rollback_submitting",
            "rollback_ambiguous",
        }:
            raise ConfirmationError(
                "A new rollback approval cannot be issued because the prior deletion "
                "outcome is unknown. Check Splitwise and resolve it manually."
            )
        if posting is None or posting.status not in {"posted", "posted_unverified"}:
            raise PostingBlockedError("No completed app-created expense is available to roll back.")
        self._validated_confirmation_plan(run)
        self._require_audited_posting_target(posting)
        return self.repository.issue_rollback_confirmation(run_id)

    def _validated_confirmation_plan(self, run: RunRecord) -> ExpensePreview:
        if (
            run.bill is None
            or run.preview is None
            or not run.preview_digest
            or run.allocation is None
            or run.household is None
            or run.gate is None
        ):
            raise PostingBlockedError("The saved confirmation plan is incomplete.")
        preview = ExpensePreview.model_validate(run.preview)
        expected_digest = confirmation_plan_digest(
            run_id=run.id,
            bill=run.bill,
            allocation=run.allocation,
            household=run.household,
            gate=run.gate,
            preview=preview,
        )
        if expected_digest != run.preview_digest:
            raise PostingBlockedError("The saved confirmation plan failed its integrity check.")
        if not self.repository.audit.contains_event(
            "run.preview_created",
            {
                "run_id": run.id,
                "bill_fingerprint": run.logical_fingerprint,
                "preview_digest": run.preview_digest,
                "blocked": False,
            },
        ):
            raise PostingBlockedError(
                "The current confirmation plan is not committed in the audit chain."
            )
        return preview

    def _require_audited_posting_target(self, posting: PostingRecord) -> None:
        if not self.repository.audit.contains_event(
            "posting.completed",
            {
                "posting_id": posting.id,
                "run_id": posting.run_id,
                "destination": posting.destination,
                "correlation_id": posting.correlation_id,
                "request_digest": posting.request_digest,
                "external_id": posting.external_id,
                "verified": posting.status == "posted",
            },
        ):
            raise PostingBlockedError("The rollback target is not committed in the audit chain.")

    def audit_status(self) -> dict[str, Any]:
        verification = self.repository.audit.verify()
        return verification.model_dump(mode="json")


def preview_request_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def confirmation_plan_digest(
    *,
    run_id: str,
    bill: NormalizedBill,
    allocation: dict[str, Any],
    household: dict[str, Any],
    gate: dict[str, Any],
    preview: ExpensePreview,
) -> str:
    """Commit to every reviewed input and the exact deterministic destination plan."""

    destination_payload = None
    if preview.destination == "splitwise" and preview.postable:
        correlation_id = posting_correlation_id(run_id, "splitwise")
        destination_payload = build_create_payload(
            preview=preview,
            correlation_id=correlation_id,
        )
    return preview_request_digest(
        {
            "schema": "waysplit-confirmation-plan-v1",
            "run_id": run_id,
            "bill": bill.model_dump(mode="json"),
            "allocation": allocation,
            "household": household,
            "gate": gate,
            "destination_preview": preview.model_dump(mode="json"),
            "destination_payload": destination_payload,
        }
    )
