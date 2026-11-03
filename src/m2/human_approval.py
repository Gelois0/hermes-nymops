"""Human-approval records: content-bound, HMAC-authenticated, single-use."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from allowed_hosts import allowed_host_set
ALLOWLIST = allowed_host_set()
MUTATING_ACTIONS = frozenset(("start", "stop", "restart", "update_binary"))
READ_ACTIONS = frozenset(
    (
        "nym_monitor",
        "nym_context_diagnostic",
        "run_nym_connectivity_check",
        "nym_journal_status",
    )
)
APPROVAL_KIND = "nymops.human_approval.v1"
APPROVAL_SECRET_ENV = "NYM_APPROVAL_SECRET"

_PLAN_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_APPROVAL_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_MAC_RE = re.compile(r"^[0-9a-f]{64}$")


class ApprovalError(ValueError):
    """Raised when an approval is missing, stale, or mismatched."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_expiry(value: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ApprovalError("invalid_expiry")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ApprovalError("invalid_expiry") from exc


def canonical_json(obj: Mapping[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def content_digest(obj: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


APPROVAL_SECRET_CREDENTIAL = "approval_secret"


def approval_secret() -> bytes | None:
    """Resolve HMAC secret from env or systemd LoadCredential file (never logged)."""
    raw = os.environ.get(APPROVAL_SECRET_ENV, "")
    if type(raw) is str and raw != "":
        if len(raw) < 32 or "\x00" in raw:
            raise ApprovalError("weak_approval_secret")
        return raw.encode("utf-8")
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if type(cred_dir) is not str or not os.path.isabs(cred_dir):
        return None
    path = os.path.join(cred_dir, APPROVAL_SECRET_CREDENTIAL)
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ApprovalError("approval_secret_unreadable") from exc
    if data.endswith(b"\n"):
        data = data[:-1]
    if data.endswith(b"\r"):
        data = data[:-1]
    if len(data) < 32 or b"\x00" in data:
        raise ApprovalError("weak_approval_secret")
    return data


def _mac_for(core: Mapping[str, Any], secret: bytes) -> str:
    return hmac.new(secret, canonical_json(core), hashlib.sha256).hexdigest()


def plan_display_fields(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Exact fields a human must see before approving execution."""
    if type(plan) is not dict:
        raise ApprovalError("invalid_plan")
    contract = plan.get("contract") if type(plan.get("contract")) is dict else {}
    release = plan.get("release") if type(plan.get("release")) is dict else None
    return {
        "plan_id": plan.get("plan_id"),
        "objective": plan.get("objective"),
        "maintenance_enabled": plan.get("maintenance_enabled"),
        "action": contract.get("action"),
        "targets": list(contract.get("targets") or []),
        "concurrency": contract.get("concurrency"),
        "expires_at": contract.get("expires_at"),
        "release": release,
        "exact_plan": json.loads(canonical_json(plan).decode("ascii")),
    }


def build_approval(
    *,
    plan: Mapping[str, Any],
    approver: str,
    issued_at: str | None = None,
    expires_at: str | None = None,
    note: str = "",
    allows_apply: bool = False,
) -> dict[str, Any]:
    """Create a single-use approval bound to one plan's identity and content."""
    display = plan_display_fields(plan)
    plan_id = display["plan_id"]
    if type(plan_id) is not str or _PLAN_ID_RE.fullmatch(plan_id) is None:
        raise ApprovalError("invalid_plan_id")
    action = display["action"]
    if action not in MUTATING_ACTIONS:
        raise ApprovalError("invalid_action")
    targets = display["targets"]
    if (
        type(targets) is not list
        or not targets
        or any(type(t) is not str or t not in ALLOWLIST for t in targets)
        or len(set(targets)) != len(targets)
    ):
        raise ApprovalError("invalid_targets")
    if display["maintenance_enabled"] is not False:
        raise ApprovalError("maintenance_flag_must_remain_false_until_m6")
    if type(allows_apply) is not bool:
        raise ApprovalError("invalid_allows_apply")

    issued = issued_at or _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    _parse_expiry(issued)
    exp = expires_at or display["expires_at"]
    if type(exp) is not str:
        raise ApprovalError("invalid_expiry")
    if _parse_expiry(exp) <= _parse_expiry(issued):
        raise ApprovalError("expiry_before_issue")

    core = {
        "kind": APPROVAL_KIND,
        "plan_id": plan_id,
        "plan_content_sha256": content_digest(
            {k: v for k, v in plan.items() if k != "plan_id"}
        ),
        "action": action,
        "targets": list(targets),
        "approver": approver,
        "issued_at": issued,
        "expires_at": exp,
        "note": note,
        "single_use": True,
        "allows_apply": allows_apply,
        "requires_explicit_restart_or_update": action in ("restart", "update_binary"),
    }
    if allows_apply:
        secret = approval_secret()
        if secret is None:
            raise ApprovalError("approval_secret_required")
        mac = _mac_for(core, secret)
        body = dict(core, mac=mac)
    else:
        body = dict(core)
    approval_id = content_digest(body)
    return dict(body, approval_id=approval_id)


def _assert_fresh(approval: Mapping[str, Any], now: datetime | None = None) -> None:
    now = now or _utc_now()
    if _parse_expiry(approval["expires_at"]) <= now:
        raise ApprovalError("approval_expired")
    if _parse_expiry(approval["issued_at"]) > now:
        raise ApprovalError("approval_not_yet_valid")


def verify_approval_for_plan(
    approval: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    now: datetime | None = None,
    consumed_ids: set[str] | None = None,
    expect_allows_apply: bool = False,
) -> dict[str, Any]:
    """Accept only an unconsumed approval that matches this exact plan."""
    if type(approval) is not dict or approval.get("kind") != APPROVAL_KIND:
        raise ApprovalError("invalid_approval")
    aid = approval.get("approval_id")
    if type(aid) is not str or _APPROVAL_ID_RE.fullmatch(aid) is None:
        raise ApprovalError("invalid_approval_id")
    body = {k: v for k, v in approval.items() if k != "approval_id"}
    if not hmac.compare_digest(aid, content_digest(body)):
        raise ApprovalError("approval_hash_mismatch")

    display = plan_display_fields(plan)
    if not hmac.compare_digest(str(approval["plan_id"]), str(display["plan_id"])):
        raise ApprovalError("plan_id_mismatch")
    expected_content = content_digest({k: v for k, v in plan.items() if k != "plan_id"})
    if not hmac.compare_digest(approval["plan_content_sha256"], expected_content):
        raise ApprovalError("plan_content_mismatch")
    if approval["action"] != display["action"]:
        raise ApprovalError("action_mismatch")
    if list(approval["targets"]) != list(display["targets"]):
        raise ApprovalError("targets_mismatch")
    if approval["action"] in ("restart", "update_binary"):
        if not approval.get("requires_explicit_restart_or_update"):
            raise ApprovalError("restart_update_requires_explicit_flag")
    if expect_allows_apply:
        if approval.get("allows_apply") is not True:
            raise ApprovalError("allows_apply_required")
        secret = approval_secret()
        if secret is None:
            raise ApprovalError("approval_secret_required")
        mac = approval.get("mac")
        if type(mac) is not str or _MAC_RE.fullmatch(mac) is None:
            raise ApprovalError("approval_mac_required")
        core = {k: v for k, v in body.items() if k != "mac"}
        expected_mac = _mac_for(core, secret)
        if not hmac.compare_digest(mac, expected_mac):
            raise ApprovalError("approval_mac_mismatch")
    elif approval.get("allows_apply") is True:
        raise ApprovalError("allows_apply_forbidden_without_maintenance_gate")

    _assert_fresh(approval, now=now)
    consumed = consumed_ids if consumed_ids is not None else set()
    if aid in consumed:
        raise ApprovalError("approval_already_consumed")
    return {"ok": True, "approval_id": aid, "plan_id": display["plan_id"]}


def consume_approval(
    approval: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    consumed_ids: set[str],
    now: datetime | None = None,
    expect_allows_apply: bool = False,
) -> dict[str, Any]:
    result = verify_approval_for_plan(
        approval,
        plan,
        now=now,
        consumed_ids=consumed_ids,
        expect_allows_apply=expect_allows_apply,
    )
    consumed_ids.add(result["approval_id"])
    return result


def require_read_grant(
    *,
    action: str,
    hosts: list[str],
    grant_text: str,
    session_grants: set[str],
) -> None:
    """Chat read tools need a fresh explicit grant string; prior grants do not carry."""
    if action not in READ_ACTIONS:
        raise ApprovalError("invalid_read_action")
    if (
        type(hosts) is not list
        or not hosts
        or any(h not in ALLOWLIST for h in hosts)
        or len(set(hosts)) != len(hosts)
    ):
        raise ApprovalError("invalid_hosts")
    token = f"{action}|{','.join(hosts)}|{grant_text.strip()}"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if digest in session_grants:
        raise ApprovalError("stale_or_reused_read_grant")
    if not grant_text.strip():
        raise ApprovalError("missing_explicit_grant")
    session_grants.add(digest)
