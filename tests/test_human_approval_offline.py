#!/usr/bin/env python3
"""Offline M2 rejection/authorization tests. No nodes, no apply, no MCP spawn."""
from __future__ import annotations

import copy
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "m2"
sys.path.insert(0, str(ROOT))

from human_approval import (  # noqa: E402
    ApprovalError,
    build_approval,
    consume_approval,
    plan_display_fields,
    require_read_grant,
    verify_approval_for_plan,
)
from change_policy_readonly_mirror import PolicyError, apply, prepare  # noqa: E402


def _plan(action: str = "restart", targets=None, hours: int = 2) -> dict:
    targets = targets or ["node-b"]
    expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    contract = prepare(
        {
            "targets": targets,
            "action": action,
            "concurrency": 1,
            "expires_at": expires,
            **({"version": "1.43.0"} if action == "update_binary" else {}),
        }
    )
    # Match preparation.py outer envelope shape used by MCP prepare_change_plan.
    content = {
        "objective": "plan_only",
        "maintenance_enabled": False,
        "contract": {k: v for k, v in contract.items()},
        "release": None,
    }
    if action == "update_binary":
        content["release"] = {
            "release_tag": "v1.43.0",
            "source_url": "https://example.invalid/nym-node",
        }
    from human_approval import content_digest

    plan = dict(content, plan_id=content_digest(content))
    return plan


def expect_fail(fn, code: str) -> None:
    try:
        fn()
    except ApprovalError as exc:
        if str(exc) != code:
            raise AssertionError(f"expected {code!r}, got {exc!r}") from exc
        return
    raise AssertionError(f"expected ApprovalError({code!r})")


def main() -> int:
    failures = 0
    results = []

    def case(name: str, fn) -> None:
        nonlocal failures
        try:
            fn()
            results.append({"case": name, "ok": True})
        except Exception as exc:
            failures += 1
            results.append(
                {
                    "case": name,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc(limit=3),
                }
            )

    def t_display_required_fields():
        plan = _plan()
        d = plan_display_fields(plan)
        for key in (
            "plan_id",
            "action",
            "targets",
            "concurrency",
            "expires_at",
            "exact_plan",
            "maintenance_enabled",
        ):
            assert key in d and d[key] is not None or key == "maintenance_enabled"
        assert d["action"] == "restart"
        assert d["targets"] == ["node-b"]
        assert d["maintenance_enabled"] is False
        assert d["exact_plan"]["plan_id"] == plan["plan_id"]

    def t_authorize_matching_restart():
        plan = _plan("restart", ["node-d"])
        appr = build_approval(plan=plan, approver="operator", note="M2 offline authorize")
        out = verify_approval_for_plan(appr, plan)
        assert out["ok"] is True
        assert out["plan_id"] == plan["plan_id"]

    def t_reject_stale_approval_on_other_plan():
        plan_a = _plan("restart", ["node-a"])
        plan_b = _plan("restart", ["node-e"])
        appr = build_approval(plan=plan_a, approver="operator")
        expect_fail(lambda: verify_approval_for_plan(appr, plan_b), "plan_id_mismatch")

    def t_reject_tampered_plan_content():
        plan = _plan("restart", ["node-f"])
        appr = build_approval(plan=plan, approver="operator")
        tampered = copy.deepcopy(plan)
        # Keep plan_id field stale while altering content → content binding must fail.
        tampered["objective"] = "tampered"
        expect_fail(
            lambda: verify_approval_for_plan(appr, tampered), "plan_content_mismatch"
        )

    def t_reject_action_swap_restart_to_update():
        plan_r = _plan("restart", ["node-b"])
        appr = build_approval(plan=plan_r, approver="operator")
        plan_u = _plan("update_binary", ["node-b"])
        expect_fail(lambda: verify_approval_for_plan(appr, plan_u), "plan_id_mismatch")

    def t_reject_expired_approval():
        plan = _plan("restart", ["node-c"], hours=2)
        past = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        mid = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        # issued_at must be < expires_at; craft manually after build with short window
        appr = build_approval(
            plan=plan,
            approver="operator",
            issued_at=past,
            expires_at=mid,
        )
        expect_fail(lambda: verify_approval_for_plan(appr, plan), "approval_expired")

    def t_single_use_consumption():
        plan = _plan("restart", ["node-d"])
        appr = build_approval(plan=plan, approver="operator")
        consumed: set[str] = set()
        consume_approval(appr, plan, consumed_ids=consumed)
        expect_fail(
            lambda: consume_approval(appr, plan, consumed_ids=consumed),
            "approval_already_consumed",
        )

    def t_explicit_restart_update_flag_required():
        plan = _plan("restart", ["node-a"])
        appr = build_approval(plan=plan, approver="operator")
        broken = dict(appr)
        broken["requires_explicit_restart_or_update"] = False
        # recompute id so hash matches body but flag wrong
        from human_approval import content_digest

        body = {k: v for k, v in broken.items() if k != "approval_id"}
        broken["approval_id"] = content_digest(body)
        expect_fail(
            lambda: verify_approval_for_plan(broken, plan),
            "restart_update_requires_explicit_flag",
        )

    def t_allows_apply_forbidden():
        plan = _plan("restart", ["node-b"])
        appr = build_approval(plan=plan, approver="operator")
        evil = dict(appr)
        evil["allows_apply"] = True
        from human_approval import content_digest

        body = {k: v for k, v in evil.items() if k != "approval_id"}
        evil["approval_id"] = content_digest(body)
        expect_fail(
            lambda: verify_approval_for_plan(evil, plan),
            "allows_apply_forbidden_without_maintenance_gate",
        )

    def t_apply_still_blocked():
        try:
            apply({"plan_id": "x"})
        except PolicyError as exc:
            assert str(exc) == "maintenance_disabled"
            return
        raise AssertionError("apply must raise maintenance_disabled")

    def t_read_grant_no_reuse():
        session: set[str] = set()
        require_read_grant(
            action="nym_monitor",
            hosts=["node-b"],
            grant_text="approve monitor node-b once",
            session_grants=session,
        )
        expect_fail(
            lambda: require_read_grant(
                action="nym_monitor",
                hosts=["node-b"],
                grant_text="approve monitor node-b once",
                session_grants=session,
            ),
            "stale_or_reused_read_grant",
        )

    for name, fn in [
        ("display_required_fields", t_display_required_fields),
        ("authorize_matching_restart", t_authorize_matching_restart),
        ("reject_stale_approval_other_plan", t_reject_stale_approval_on_other_plan),
        ("reject_tampered_plan_content", t_reject_tampered_plan_content),
        ("reject_action_swap", t_reject_action_swap_restart_to_update),
        ("reject_expired_approval", t_reject_expired_approval),
        ("single_use_consumption", t_single_use_consumption),
        ("explicit_restart_update_flag", t_explicit_restart_update_flag_required),
        ("allows_apply_forbidden", t_allows_apply_forbidden),
        ("apply_still_blocked", t_apply_still_blocked),
        ("read_grant_no_reuse", t_read_grant_no_reuse),
    ]:
        case(name, fn)

    import json
    from datetime import datetime as dt

    report = {
        "captured_at": dt.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "failures": failures,
        "passed": len(results) - failures,
        "total": len(results),
        "results": results,
    }
    out = Path(__file__).resolve().parent / ".m2-offline-test-evidence.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "failed": failures, "evidence": str(out)}))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
