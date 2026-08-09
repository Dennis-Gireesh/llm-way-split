from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from waysplit.domain.money import format_money
from waysplit.errors import DestinationError
from waysplit.household import ExpensePreview

DEFAULT_API_BASE = "https://secure.splitwise.com/api/v3.0"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class AmbiguousDestinationError(DestinationError):
    """The external service may have accepted a non-idempotent request."""


@dataclass(frozen=True, slots=True)
class CreatedExpense:
    expense_id: str
    verified: bool
    verification_issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SplitwiseMember:
    user_id: int
    display_name: str


@dataclass(frozen=True, slots=True)
class SplitwiseGroup:
    group_id: int
    name: str
    members: tuple[SplitwiseMember, ...]


@dataclass(frozen=True, slots=True)
class SplitwiseContext:
    current_user: SplitwiseMember
    groups: tuple[SplitwiseGroup, ...]


class SplitwiseClient:
    def __init__(self, *, access_token: str, api_base: str = DEFAULT_API_BASE) -> None:
        if not access_token.strip():
            raise DestinationError("A Splitwise access token is required.")
        self.access_token = access_token
        self.api_base = api_base.rstrip("/")

    async def account_context(self) -> SplitwiseContext:
        headers = self._headers()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        ) as client:
            user_payload, groups_payload = await _read_context(client, self.api_base)

        raw_user = user_payload.get("user")
        if not isinstance(raw_user, dict):
            raise DestinationError("Splitwise did not return the current account.")
        current_user = _member_from_json(raw_user)
        raw_groups = groups_payload.get("groups")
        if not isinstance(raw_groups, list):
            raise DestinationError("Splitwise did not return any group information.")
        groups: list[SplitwiseGroup] = []
        for item in raw_groups[:100]:
            if (
                not isinstance(item, dict)
                or isinstance(item.get("id"), bool)
                or not isinstance(item.get("id"), int)
                or item["id"] <= 0
            ):
                continue
            raw_members = item.get("members")
            members: list[SplitwiseMember] = []
            if isinstance(raw_members, list):
                for raw_member in raw_members[:100]:
                    if isinstance(raw_member, dict):
                        try:
                            members.append(_member_from_json(raw_member))
                        except DestinationError:
                            continue
            name = str(item.get("name") or f"Group {item['id']}")[:160]
            groups.append(
                SplitwiseGroup(
                    group_id=item["id"],
                    name=name,
                    members=tuple(members),
                )
            )
        return SplitwiseContext(current_user=current_user, groups=tuple(groups))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "User-Agent": "WaySplit/0.1 (+https://github.com/Dennis-Gireesh/llm-way-split)",
        }

    async def create_expense(
        self, *, preview: ExpensePreview, correlation_id: str
    ) -> CreatedExpense:
        payload = build_create_payload(preview=preview, correlation_id=correlation_id)
        headers = self._headers()
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        ) as client:
            body = await _non_idempotent_post(
                client,
                f"{self.api_base}/create_expense",
                payload,
            )
            errors = body.get("errors")
            if errors:
                raise DestinationError(
                    "Splitwise rejected the expense. Review the account IDs and shares."
                )
            expenses = body.get("expenses")
            if not isinstance(expenses, list) or len(expenses) != 1:
                raise AmbiguousDestinationError(
                    "Splitwise did not return exactly one expense. Check the group before retrying."
                )
            expense = expenses[0]
            if not isinstance(expense, dict):
                raise AmbiguousDestinationError(
                    "Splitwise accepted the request without a usable expense ID. "
                    "Check the group manually."
                )
            expense_id = _positive_expense_id(expense.get("id"))
            if expense_id is None:
                raise AmbiguousDestinationError(
                    "Splitwise accepted the request without a positive numeric expense ID. "
                    "Check the group manually."
                )

            verification_issues = _verify_expense(
                expense,
                preview,
                correlation_id,
                expected_expense_id=expense_id,
            )
            try:
                get_status, get_body = await _bounded_get_json(
                    client, f"{self.api_base}/get_expense/{expense_id}"
                )
                if get_status == 200:
                    retrieved = get_body.get("expense") if isinstance(get_body, dict) else None
                    if isinstance(retrieved, dict):
                        verification_issues = _verify_expense(
                            retrieved,
                            preview,
                            correlation_id,
                            expected_expense_id=expense_id,
                        )
                    else:
                        verification_issues = (
                            *verification_issues,
                            "Retrieval response was incomplete.",
                        )
                else:
                    verification_issues = (
                        *verification_issues,
                        f"Retrieval returned HTTP {get_status}.",
                    )
            except (httpx.HTTPError, ValueError, json.JSONDecodeError, DestinationError):
                verification_issues = (
                    *verification_issues,
                    "Retrieval verification was unavailable.",
                )

        return CreatedExpense(
            expense_id=expense_id,
            verified=not verification_issues,
            verification_issues=tuple(dict.fromkeys(verification_issues)),
        )

    async def verify_expense(
        self,
        *,
        expense_id: str,
        preview: ExpensePreview,
        correlation_id: str,
    ) -> tuple[str, ...]:
        if _positive_expense_id(expense_id) is None:
            raise DestinationError("The stored Splitwise expense ID is invalid.")
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
            headers=self._headers(),
        ) as client:
            status_code, body = await _bounded_get_json(
                client, f"{self.api_base}/get_expense/{expense_id}"
            )
        if status_code == 404:
            raise DestinationError("The recorded Splitwise expense no longer exists.")
        if status_code in {401, 403}:
            raise DestinationError("Splitwise rejected the access token.")
        if status_code != 200:
            raise DestinationError(f"Splitwise expense verification failed (HTTP {status_code}).")
        expense = body.get("expense") if isinstance(body, dict) else None
        if not isinstance(expense, dict):
            raise DestinationError("Splitwise returned incomplete expense information.")
        return _verify_expense(
            expense,
            preview,
            correlation_id,
            expected_expense_id=expense_id,
        )

    async def delete_expense(self, expense_id: str) -> None:
        if not expense_id.isdigit():
            raise DestinationError("The stored Splitwise expense ID is invalid.")
        headers = self._headers()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        ) as client:
            body = await _non_idempotent_post(
                client,
                f"{self.api_base}/delete_expense/{expense_id}",
                {},
            )
        if not isinstance(body, dict) or body.get("success") is not True:
            raise DestinationError("Splitwise did not delete the expense.")


def build_create_payload(*, preview: ExpensePreview, correlation_id: str) -> dict[str, Any]:
    if not preview.postable:
        raise DestinationError("The preview still contains posting blockers.")
    if preview.group_id is None:
        raise DestinationError("A Splitwise group ID is required.")
    if not correlation_id.startswith("WS-"):
        raise DestinationError("The posting correlation ID is invalid.")

    payload: dict[str, Any] = {
        "cost": format_money(preview.cost),
        "description": preview.description,
        "details": f"{preview.details}\nWaySplit reference: {correlation_id}",
        "date": preview.date,
        "currency_code": preview.currency_code,
        "group_id": preview.group_id,
    }
    included = [share for share in preview.shares if share.paid_share != 0 or share.owed_share != 0]
    for index, share in enumerate(included):
        if share.splitwise_user_id is None:
            raise DestinationError(f"{share.participant_name} is missing a Splitwise user ID.")
        prefix = f"users__{index}__"
        payload[f"{prefix}user_id"] = share.splitwise_user_id
        payload[f"{prefix}paid_share"] = format_money(share.paid_share)
        payload[f"{prefix}owed_share"] = format_money(share.owed_share)

    paid_total = sum((share.paid_share for share in included), start=Decimal("0.00"))
    owed_total = sum((share.owed_share for share in included), start=Decimal("0.00"))
    if paid_total != preview.cost or owed_total != preview.cost:
        raise DestinationError("Paid and owed shares must each equal the expense cost.")
    return payload


async def _read_context(
    client: httpx.AsyncClient, api_base: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        user_result, groups_result = await asyncio.gather(
            _bounded_get_json(client, f"{api_base}/get_current_user"),
            _bounded_get_json(client, f"{api_base}/get_groups"),
        )
    except httpx.HTTPError as exc:
        raise DestinationError("Could not read the Splitwise account context.") from exc
    for status_code, _body in (user_result, groups_result):
        if status_code in {401, 403}:
            raise DestinationError("Splitwise rejected the access token.")
        if status_code >= 400:
            raise DestinationError(f"Splitwise account lookup failed (HTTP {status_code}).")
    user_payload = user_result[1]
    groups_payload = groups_result[1]
    if not isinstance(user_payload, dict) or not isinstance(groups_payload, dict):
        raise DestinationError("Splitwise returned incomplete account information.")
    return user_payload, groups_payload


def _member_from_json(value: dict[str, Any]) -> SplitwiseMember:
    user_id = value.get("id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise DestinationError("Splitwise returned a member without an ID.")
    parts = [
        item.strip()
        for item in (value.get("first_name"), value.get("last_name"))
        if isinstance(item, str) and item.strip()
    ]
    display_name = " ".join(part for part in parts if part) or f"User {user_id}"
    return SplitwiseMember(user_id=user_id, display_name=display_name[:160])


async def _non_idempotent_post(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        async with client.stream("POST", url, json=payload) as response:
            status_code = response.status_code
            raw_body = await _bounded_response_bytes(response)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise DestinationError(
            "Could not connect to Splitwise; no automatic retry was attempted."
        ) from exc
    except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
        raise AmbiguousDestinationError(
            "The Splitwise result is unknown. Check the group for the WaySplit reference "
            "before retrying."
        ) from exc
    except httpx.HTTPError as exc:
        raise AmbiguousDestinationError(
            "The Splitwise connection ended unexpectedly. Check the group before retrying."
        ) from exc

    if 400 <= status_code < 500:
        raise DestinationError(f"Splitwise rejected the request (HTTP {status_code}).")
    if status_code >= 500:
        raise AmbiguousDestinationError(
            f"Splitwise returned HTTP {status_code}; the result may be unknown."
        )
    try:
        body = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise AmbiguousDestinationError(
            "Splitwise returned an unreadable result. Check the group before retrying."
        ) from exc
    if not isinstance(body, dict):
        raise AmbiguousDestinationError(
            "Splitwise returned an incomplete result. Check the group before retrying."
        )
    return body


def _verify_expense(
    expense: dict[str, Any],
    preview: ExpensePreview,
    correlation_id: str,
    *,
    expected_expense_id: str,
) -> tuple[str, ...]:
    issues: list[str] = []
    if _positive_expense_id(expense.get("id")) != expected_expense_id:
        issues.append("Returned expense ID does not match the created expense.")
    try:
        returned_cost = Decimal(str(expense.get("cost")))
    except (InvalidOperation, ValueError):
        returned_cost = Decimal("NaN")
    if not returned_cost.is_finite() or returned_cost != preview.cost:
        issues.append("Returned cost does not match the preview.")
    if expense.get("currency_code") != preview.currency_code:
        issues.append("Returned currency does not match the preview.")
    if str(expense.get("group_id")) != str(preview.group_id):
        issues.append("Returned group does not match the preview.")
    if expense.get("description") != preview.description:
        issues.append("Returned description does not match the preview.")
    expected_details = f"{preview.details}\nWaySplit reference: {correlation_id}"
    if expense.get("details") != expected_details:
        issues.append("Returned notes do not match the preview and posting reference.")
    returned_date = str(expense.get("date") or "")[:10]
    if returned_date != preview.date[:10]:
        issues.append("Returned date does not match the preview.")

    expected = {
        share.splitwise_user_id: (share.paid_share, share.owed_share)
        for share in preview.shares
        if share.splitwise_user_id is not None and (share.paid_share != 0 or share.owed_share != 0)
    }
    returned: dict[int, tuple[Decimal, Decimal]] = {}
    raw_users = expense.get("users")
    malformed_users = False
    if isinstance(raw_users, list) and len(raw_users) <= 200:
        if len(raw_users) != len(expected):
            malformed_users = True
        for item in raw_users:
            if not isinstance(item, dict):
                malformed_users = True
                continue
            user = item.get("user")
            user_id = user.get("id") if isinstance(user, dict) else item.get("user_id")
            if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id in returned:
                malformed_users = True
                continue
            try:
                paid = Decimal(str(item.get("paid_share")))
                owed = Decimal(str(item.get("owed_share")))
            except (InvalidOperation, ValueError):
                malformed_users = True
                continue
            if not paid.is_finite() or not owed.is_finite():
                malformed_users = True
                continue
            returned[user_id] = (paid, owed)
    else:
        malformed_users = True
    if malformed_users or returned != expected:
        issues.append("Returned participant shares do not match the preview.")
    return tuple(issues)


def _positive_expense_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if isinstance(value, str) and value.isdigit() and value == str(int(value)) and int(value) > 0:
        return value
    return None


async def _bounded_response_bytes(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
            raise AmbiguousDestinationError(
                "Splitwise returned more data than the safe response limit; the outcome is unknown."
            )
        body.extend(chunk)
    return bytes(body)


async def _bounded_get_json(client: httpx.AsyncClient, url: str) -> tuple[int, dict[str, Any]]:
    try:
        async with client.stream("GET", url) as response:
            status_code = response.status_code
            raw_body = await _bounded_response_bytes(response)
    except AmbiguousDestinationError as exc:
        raise DestinationError("Splitwise returned too much account data.") from exc
    try:
        body = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise DestinationError("Splitwise returned unreadable account information.") from exc
    if not isinstance(body, dict):
        raise DestinationError("Splitwise returned incomplete account information.")
    return status_code, body
