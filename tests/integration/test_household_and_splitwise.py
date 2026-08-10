from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from waysplit.destinations.splitwise import (
    AmbiguousDestinationError,
    SplitwiseClient,
    SplitwiseContext,
    SplitwiseGroup,
    SplitwiseMember,
    build_create_payload,
)
from waysplit.domain.allocation import allocate_bill
from waysplit.domain.models import NormalizedBill
from waysplit.errors import DestinationError
from waysplit.household import HouseholdConfig, Participant, build_expense_preview
from waysplit.service import _ignore_prior_cycle_payment, confirmation_plan_digest


def _household(*, postable: bool) -> HouseholdConfig:
    return HouseholdConfig(
        participants=(
            Participant(
                id="member-alpha",
                name="Member Alpha",
                weight="1",
                splitwise_user_id=101 if postable else None,
            ),
            Participant(
                id="member-beta",
                name="Member Beta",
                weight="1",
                splitwise_user_id=202 if postable else None,
            ),
        ),
        service_owners={
            "service-alpha": "member-alpha",
            "service-beta": "member-beta",
        },
        payer_participant_id="member-alpha" if postable else None,
        splitwise_group_id=44 if postable else None,
    )


def _preview(bill: NormalizedBill, *, postable: bool = True):
    household = _household(postable=postable)
    allocation = allocate_bill(bill, household.allocation_rules())
    return build_expense_preview(
        bill=bill,
        allocation=allocation,
        household=household,
    )


def test_local_summary_does_not_require_splitwise_account(normalized_bill: NormalizedBill) -> None:
    household = HouseholdConfig(
        participants=(
            Participant(id="member-alpha", name="Member Alpha", weight="1"),
            Participant(id="member-beta", name="Member Beta", weight="1"),
        ),
        service_owners={
            "service-alpha": "member-alpha",
            "service-beta": "member-beta",
        },
        output_destination="local_summary",
    )
    allocation = allocate_bill(normalized_bill, household.allocation_rules())
    preview = build_expense_preview(
        bill=normalized_bill,
        allocation=allocation,
        household=household,
    )
    assert preview.destination == "local_summary"
    assert preview.postable
    assert not preview.blockers
    assert sum((share.owed_share for share in preview.shares), Decimal("0")) == preview.cost
    digest = confirmation_plan_digest(
        run_id="local-summary-run",
        bill=normalized_bill,
        allocation=allocation.model_dump(mode="json"),
        household=household.json_safe(),
        gate={"status": "approved"},
        preview=preview,
    )
    assert len(digest) == 64


def test_prior_cycle_payment_does_not_reduce_a_new_statement_total(
    normalized_bill: NormalizedBill,
) -> None:
    bill = normalized_bill.model_copy(
        update={
            "totals": normalized_bill.totals.model_copy(
                update={
                    "balance_forward": Decimal("0.00"),
                    "payments_and_credits": Decimal("-414.84"),
                    "current_charges": Decimal("415.10"),
                    "amount_due": Decimal("415.10"),
                }
            )
        }
    )
    repaired = _ignore_prior_cycle_payment(bill)
    assert repaired.totals.payments_and_credits == Decimal("0.00")
    assert repaired.totals.current_charges == repaired.totals.amount_due


def _remote_expense(
    bill: NormalizedBill,
    *,
    correlation_id: str = "WS-ABCDEF123456",
    expense_id: int = 7001,
) -> dict[str, object]:
    preview = _preview(bill)
    payload = build_create_payload(preview=preview, correlation_id=correlation_id)
    return {
        "id": expense_id,
        "cost": payload["cost"],
        "description": payload["description"],
        "details": payload["details"],
        "date": payload["date"],
        "currency_code": payload["currency_code"],
        "group_id": payload["group_id"],
        "users": [
            {
                "user": {"id": 101},
                "paid_share": "45.00",
                "owed_share": "31.50",
            },
            {
                "user": {"id": 202},
                "paid_share": "0.00",
                "owed_share": "13.50",
            },
        ],
    }


def test_household_preview_exposes_every_destination_blocker(
    normalized_bill: NormalizedBill,
) -> None:
    preview = _preview(normalized_bill, postable=False)

    assert preview.postable is False
    assert preview.cost == Decimal("45.00")
    assert preview.blockers == (
        "Choose which household member paid the statement.",
        "Add a Splitwise group ID (use 0 for an expense outside a group).",
        "Add a Splitwise user ID for Member Alpha.",
        "Add a Splitwise user ID for Member Beta.",
    )


def test_household_rejects_duplicate_splitwise_user_mappings() -> None:
    with pytest.raises(ValueError, match="exactly one participant"):
        HouseholdConfig(
            participants=(
                Participant(
                    id="member-alpha",
                    name="Member Alpha",
                    weight="1",
                    splitwise_user_id=101,
                ),
                Participant(
                    id="member-beta",
                    name="Member Beta",
                    weight="1",
                    splitwise_user_id=101,
                ),
            ),
            payer_participant_id="member-alpha",
            splitwise_group_id=44,
        )


@pytest.mark.parametrize(
    "field",
    [
        {"splitwise_user_id": True},
        {"splitwise_group_id": True},
    ],
)
def test_household_rejects_boolean_destination_ids(field: dict[str, bool]) -> None:
    participant_values: dict[str, object] = {
        "id": "member-alpha",
        "name": "Member Alpha",
        "weight": "1",
    }
    household_values: dict[str, object] = {
        "participants": (participant_values,),
        "splitwise_group_id": 44,
    }
    if "splitwise_user_id" in field:
        participant_values.update(field)
    else:
        household_values.update(field)

    with pytest.raises(ValueError, match=r"positive integer|non-negative integer"):
        HouseholdConfig.model_validate(household_values)


def test_preview_requires_destination_id_for_zero_owed_payer(
    normalized_bill: NormalizedBill,
) -> None:
    line_charges = tuple(
        charge for charge in normalized_bill.charges if charge.scope.value == "line"
    )
    bill = normalized_bill.model_copy(
        update={
            "charges": line_charges,
            "totals": normalized_bill.totals.model_copy(
                update={
                    "current_charges": Decimal("42.00"),
                    "amount_due": Decimal("42.00"),
                }
            ),
        }
    )
    household = HouseholdConfig(
        participants=(
            Participant(id="payer", name="Payer", weight="1"),
            Participant(
                id="owner",
                name="Owner",
                weight="1",
                splitwise_user_id=202,
            ),
        ),
        service_owners={
            "service-alpha": "owner",
            "service-beta": "owner",
        },
        payer_participant_id="payer",
        splitwise_group_id=44,
    )
    allocation = allocate_bill(bill, household.allocation_rules())

    preview = build_expense_preview(
        bill=bill,
        allocation=allocation,
        household=household,
    )

    assert preview.postable is False
    assert "Add a Splitwise user ID for Payer." in preview.blockers


def test_splitwise_payload_is_exactly_flattened_and_cent_preserving(
    normalized_bill: NormalizedBill,
) -> None:
    preview = _preview(normalized_bill)

    payload = build_create_payload(preview=preview, correlation_id="WS-ABCDEF123456")

    assert payload == {
        "cost": "45.00",
        "description": "Mobile statement · Example Mobile · 2026-07-31",
        "details": (
            "WaySplit reconciled preview\n"
            "Member Alpha: USD 31.50\n"
            "Member Beta: USD 13.50\n"
            "WaySplit reference: WS-ABCDEF123456"
        ),
        "date": "2026-08-05T12:00:00Z",
        "currency_code": "USD",
        "group_id": 44,
        "users__0__user_id": 101,
        "users__0__paid_share": "45.00",
        "users__0__owed_share": "31.50",
        "users__1__user_id": 202,
        "users__1__paid_share": "0.00",
        "users__1__owed_share": "13.50",
    }


def test_splitwise_payload_refuses_a_blocked_preview(
    normalized_bill: NormalizedBill,
) -> None:
    with pytest.raises(DestinationError, match="posting blockers"):
        build_create_payload(
            preview=_preview(normalized_bill, postable=False),
            correlation_id="WS-ABCDEF123456",
        )


@pytest.mark.asyncio
@respx.mock
async def test_splitwise_create_is_verified_against_every_material_field(
    normalized_bill: NormalizedBill,
) -> None:
    expense = _remote_expense(normalized_bill)
    create_route = respx.post("https://secure.splitwise.com/api/v3.0/create_expense").mock(
        return_value=httpx.Response(200, json={"expenses": [expense]})
    )
    get_route = respx.get("https://secure.splitwise.com/api/v3.0/get_expense/7001").mock(
        return_value=httpx.Response(200, json={"expense": expense})
    )

    created = await SplitwiseClient(access_token="synthetic-access-token").create_expense(
        preview=_preview(normalized_bill),
        correlation_id="WS-ABCDEF123456",
    )

    assert created.expense_id == "7001"
    assert created.verified is True
    assert created.verification_issues == ()
    assert create_route.call_count == 1
    assert get_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_splitwise_readback_mismatch_is_never_reported_verified(
    normalized_bill: NormalizedBill,
) -> None:
    created_expense = _remote_expense(normalized_bill)
    wrong_expense = {**created_expense, "group_id": 99, "date": "2026-08-06T12:00:00Z"}
    respx.post("https://secure.splitwise.com/api/v3.0/create_expense").mock(
        return_value=httpx.Response(200, json={"expenses": [created_expense]})
    )
    respx.get("https://secure.splitwise.com/api/v3.0/get_expense/7001").mock(
        return_value=httpx.Response(200, json={"expense": wrong_expense})
    )

    created = await SplitwiseClient(access_token="synthetic-access-token").create_expense(
        preview=_preview(normalized_bill),
        correlation_id="WS-ABCDEF123456",
    )

    assert created.verified is False
    assert "Returned group does not match the preview." in created.verification_issues
    assert "Returned date does not match the preview." in created.verification_issues


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "extra_user",
    [
        {"user": {"id": 303}, "paid_share": "0.00", "owed_share": "0.00"},
        {"user": {"id": 101}, "paid_share": "45.00", "owed_share": "31.50"},
    ],
)
async def test_splitwise_readback_rejects_extra_or_duplicate_users(
    normalized_bill: NormalizedBill,
    extra_user: dict[str, object],
) -> None:
    expense = _remote_expense(normalized_bill)
    returned = {**expense, "users": [*expense["users"], extra_user]}  # type: ignore[misc]
    respx.post("https://secure.splitwise.com/api/v3.0/create_expense").mock(
        return_value=httpx.Response(200, json={"expenses": [expense]})
    )
    respx.get("https://secure.splitwise.com/api/v3.0/get_expense/7001").mock(
        return_value=httpx.Response(200, json={"expense": returned})
    )

    created = await SplitwiseClient(access_token="synthetic-access-token").create_expense(
        preview=_preview(normalized_bill),
        correlation_id="WS-ABCDEF123456",
    )

    assert created.verified is False
    assert "Returned participant shares do not match the preview." in created.verification_issues


@pytest.mark.asyncio
@respx.mock
async def test_splitwise_rollback_verifies_target_then_deletes_once(
    normalized_bill: NormalizedBill,
) -> None:
    expense = _remote_expense(normalized_bill)
    get_route = respx.get("https://secure.splitwise.com/api/v3.0/get_expense/7001").mock(
        return_value=httpx.Response(200, json={"expense": expense})
    )
    delete_route = respx.post("https://secure.splitwise.com/api/v3.0/delete_expense/7001").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    client = SplitwiseClient(access_token="synthetic-access-token")

    issues = await client.verify_expense(
        expense_id="7001",
        preview=_preview(normalized_bill),
        correlation_id="WS-ABCDEF123456",
    )
    await client.delete_expense("7001")

    assert issues == ()
    assert get_route.call_count == 1
    assert delete_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_splitwise_rollback_timeout_is_ambiguous_and_not_retried() -> None:
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    route = respx.post("https://secure.splitwise.com/api/v3.0/delete_expense/7001").mock(
        side_effect=time_out
    )

    with pytest.raises(AmbiguousDestinationError, match="result is unknown"):
        await SplitwiseClient(access_token="synthetic-access-token").delete_expense("7001")

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_splitwise_200_response_with_errors_is_still_a_failure(
    normalized_bill: NormalizedBill,
) -> None:
    route = respx.post("https://secure.splitwise.com/api/v3.0/create_expense").mock(
        return_value=httpx.Response(
            200,
            json={
                "errors": {"base": ["Synthetic validation failure"]},
                "expenses": [],
            },
        )
    )
    client = SplitwiseClient(access_token="synthetic-access-token")

    with pytest.raises(DestinationError, match="rejected the expense"):
        await client.create_expense(
            preview=_preview(normalized_bill),
            correlation_id="WS-ABCDEF123456",
        )

    assert route.call_count == 1
    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_splitwise_read_timeout_is_ambiguous_and_never_retried(
    normalized_bill: NormalizedBill,
) -> None:
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    route = respx.post("https://secure.splitwise.com/api/v3.0/create_expense").mock(
        side_effect=time_out
    )
    client = SplitwiseClient(access_token="synthetic-access-token")

    with pytest.raises(AmbiguousDestinationError, match="result is unknown"):
        await client.create_expense(
            preview=_preview(normalized_bill),
            correlation_id="WS-ABCDEF123456",
        )

    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_pathologically_nested_create_response_is_ambiguous(
    normalized_bill: NormalizedBill,
) -> None:
    nested_body = ('{"nested":' * 10_000 + "null" + "}" * 10_000).encode()
    route = respx.post("https://secure.splitwise.com/api/v3.0/create_expense").mock(
        return_value=httpx.Response(
            200,
            content=nested_body,
            headers={"Content-Type": "application/json"},
        )
    )

    with pytest.raises(AmbiguousDestinationError, match="unreadable result"):
        await SplitwiseClient(access_token="synthetic-access-token").create_expense(
            preview=_preview(normalized_bill),
            correlation_id="WS-ABCDEF123456",
        )

    assert route.call_count == 1
    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_splitwise_connect_failure_is_definite_and_never_retried(
    normalized_bill: NormalizedBill,
) -> None:
    def fail_to_connect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic connect failure", request=request)

    route = respx.post("https://secure.splitwise.com/api/v3.0/create_expense").mock(
        side_effect=fail_to_connect
    )
    client = SplitwiseClient(access_token="synthetic-access-token")

    with pytest.raises(DestinationError, match="no automatic retry"):
        await client.create_expense(
            preview=_preview(normalized_bill),
            correlation_id="WS-ABCDEF123456",
        )

    assert route.call_count == 1
    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_splitwise_account_context_returns_only_names_and_ids() -> None:
    respx.get("https://secure.splitwise.com/api/v3.0/get_current_user").mock(
        return_value=httpx.Response(
            200,
            json={
                "user": {
                    "id": 101,
                    "first_name": "Member",
                    "last_name": "Alpha",
                    "email": "must-not-be-returned@example.invalid",
                }
            },
        )
    )
    respx.get("https://secure.splitwise.com/api/v3.0/get_groups").mock(
        return_value=httpx.Response(
            200,
            json={
                "groups": [
                    {
                        "id": 44,
                        "name": "Synthetic household",
                        "members": [
                            {
                                "id": 101,
                                "first_name": "Member",
                                "last_name": "Alpha",
                                "email": "must-not-be-returned@example.invalid",
                            },
                            {
                                "id": 202,
                                "first_name": "Member",
                                "last_name": "Beta",
                            },
                        ],
                    }
                ]
            },
        )
    )

    context = await SplitwiseClient(access_token="synthetic-access-token").account_context()

    assert context == SplitwiseContext(
        current_user=SplitwiseMember(user_id=101, display_name="Member Alpha"),
        groups=(
            SplitwiseGroup(
                group_id=44,
                name="Synthetic household",
                members=(
                    SplitwiseMember(user_id=101, display_name="Member Alpha"),
                    SplitwiseMember(user_id=202, display_name="Member Beta"),
                ),
            ),
        ),
    )
    assert "must-not-be-returned" not in repr(context)
