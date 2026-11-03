"""Offline preparation + gated apply (M6). Default remains maintenance_disabled."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone

from allowed_hosts import allowed_host_set
TARGETS = allowed_host_set()
ACTIONS = frozenset(("start", "stop", "restart", "update_binary"))
MAINTENANCE_ENV = "NYM_MAINTENANCE_ENABLED"
ACTION_TO_OP = {
    "restart": "restart_nym_node",
    "update_binary": "update_nym_node",
}


class PolicyError(ValueError):
    """Closed-policy rejection; never a request to run a command."""


def parse_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PolicyError("duplicate_argument")
            result[key] = value
        return result

    def invalid_constant(value):
        raise PolicyError("invalid_json_constant")

    if type(text) is not str:
        raise PolicyError("invalid_json")
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise PolicyError("invalid_json") from exc


def _checked(request, now):
    if type(request) is not dict:
        raise PolicyError("invalid_request")
    action = request.get("action")
    if type(action) is not str or action not in ACTIONS:
        raise PolicyError("invalid_action")
    fields = {"targets", "action", "concurrency", "expires_at"}
    if action == "update_binary":
        fields.add("version")
    if set(request) != fields:
        raise PolicyError("unexpected_or_missing_argument")
    targets = request["targets"]
    if (
        type(targets) is not list
        or not targets
        or any(type(t) is not str or t not in TARGETS for t in targets)
        or len(set(targets)) != len(targets)
    ):
        raise PolicyError("invalid_targets")
    concurrency = request["concurrency"]
    if type(concurrency) is not int or not 1 <= concurrency <= len(targets):
        raise PolicyError("invalid_concurrency")
    # Staging M4–M6: mutating plans are single-host only.
    if action in {"start", "stop", "restart", "update_binary"} and (
        len(targets) != 1 or concurrency != 1
    ):
        raise PolicyError("invalid_concurrency")
    expiry = request["expires_at"]
    if type(expiry) is not str or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", expiry
    ) is None:
        raise PolicyError("invalid_expiry")
    try:
        expiration = datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PolicyError("invalid_expiry") from exc
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise PolicyError("invalid_clock")
    if expiration <= now:
        raise PolicyError("expired")
    result = dict(
        targets=sorted(targets),
        action=action,
        concurrency=concurrency,
        expires_at=expiry,
    )
    if action == "update_binary":
        version = request["version"]
        if type(version) is not str or re.fullmatch(
            r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version
        ) is None:
            raise PolicyError("invalid_version")
        result["version"] = version
    return result


def _identity(content):
    canonical = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def prepare(request, *, now=None):
    content = _checked(request, now)
    return dict(content, plan_id=_identity(content))


def validate(plan, *, now=None):
    if type(plan) is not dict:
        raise PolicyError("invalid_plan")
    identity = plan.get("plan_id")
    if type(identity) is not str or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
        raise PolicyError("invalid_plan_id")
    content = {k: v for k, v in plan.items() if k != "plan_id"}
    checked = _checked(content, now)
    if content != checked:
        raise PolicyError("noncanonical_plan")
    if not hmac.compare_digest(identity, _identity(checked)):
        raise PolicyError("plan_hash_mismatch")
    return dict(checked, plan_id=identity)


def maintenance_enabled() -> bool:
    return os.environ.get(MAINTENANCE_ENV, "0") == "1"


def apply(
    plan,
    approval=None,
    *,
    now=None,
    executor=None,
    audit_writer=None,
    consumed_ids=None,
):
    """Gated apply. Default env keeps maintenance_disabled. Requires envelope plan + approval."""
    hosts_hint = []
    if type(plan) is dict:
        contract = plan.get("contract") if type(plan.get("contract")) is dict else {}
        hosts_hint = [h for h in (contract.get("targets") or []) if h in TARGETS]

    if not maintenance_enabled():
        if audit_writer is not None:
            try:
                audit_writer(
                    "apply_blocked",
                    hosts_hint,
                    77,
                    extra={"reason": "maintenance_disabled"},
                )
            except Exception:
                pass
        raise PolicyError("maintenance_disabled")

    if approval is None:
        raise PolicyError("approval_required")
    if executor is None:
        raise PolicyError("executor_required")
    if audit_writer is None:
        raise PolicyError("audit_writer_required")

    import approval_store
    import preparation
    from human_approval import ApprovalError, approval_secret, consume_approval

    if approval_secret() is None:
        raise PolicyError("approval_secret_required")

    try:
        expected = preparation.validate(plan, now=now)
    except PolicyError:
        raise
    except Exception as exc:
        raise PolicyError("invalid_plan") from exc

    try:
        persisted = approval_store.load_consumed()
    except approval_store.ApprovalStoreError as exc:
        raise PolicyError(str(exc)) from exc
    if consumed_ids is None:
        consumed_ids = set(persisted)
    else:
        consumed_ids |= persisted

    try:
        consume_approval(
            approval,
            expected,
            consumed_ids=consumed_ids,
            now=now,
            expect_allows_apply=True,
        )
    except ApprovalError as exc:
        raise PolicyError(str(exc)) from exc

    action = expected["contract"]["action"]
    targets = list(expected["contract"]["targets"])
    if action not in ACTION_TO_OP:
        raise PolicyError("action_not_executable")
    if len(targets) != 1:
        raise PolicyError("invalid_targets")
    op = ACTION_TO_OP[action]
    plan_id = expected["plan_id"]
    approval_id = approval.get("approval_id")

    if action == "update_binary":
        release = expected.get("release") or {}
        digest = release.get("sha256")
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise PolicyError("invalid_sha256")
        os.environ["NYM_UPDATE_VERSION"] = str(expected["contract"]["version"])
        os.environ["NYM_UPDATE_TAG"] = str(release["release_tag"])
        os.environ["NYM_UPDATE_URL"] = str(release["source_url"])
        os.environ["NYM_UPDATE_SHA256"] = digest

    # Persist consumption and audit BEFORE mutation. Fail closed if either fails.
    try:
        approval_store.record_consumed(str(approval_id), plan_id=str(plan_id))
    except approval_store.ApprovalStoreError as exc:
        raise PolicyError(str(exc)) from exc
    audit_writer(
        "approval_recorded",
        targets,
        0,
        extra={
            "approval_id": str(approval_id),
            "plan_id": str(plan_id),
            "action": str(action),
        },
    )

    previous = os.environ.get("NYM_ALLOW_MUTATION")
    os.environ["NYM_ALLOW_MUTATION"] = "1"
    try:
        result = executor(op, targets)
    finally:
        if previous is None:
            os.environ.pop("NYM_ALLOW_MUTATION", None)
        else:
            os.environ["NYM_ALLOW_MUTATION"] = previous
        for key in (
            "NYM_UPDATE_VERSION",
            "NYM_UPDATE_TAG",
            "NYM_UPDATE_URL",
            "NYM_UPDATE_SHA256",
        ):
            os.environ.pop(key, None)

    if type(result) is not dict:
        raise PolicyError("executor_failure")
    return {
        "ok": bool(result.get("ok")),
        "operation": op,
        "targets": targets,
        "plan_id": plan_id,
        "approval_id": approval_id,
        "result": result,
    }
