"""Closed offline MCP adapters for Nym preparation; no runner or I/O calls."""
from __future__ import annotations

import json

import preparation
from change_policy import PolicyError

HOSTS = tuple(sorted(preparation.HOSTS))

_HOST_ARRAY = {
    "type": "array", "items": {"type": "string", "enum": list(HOSTS)},
    "minItems": 1, "maxItems": len(HOSTS), "uniqueItems": True,
}
_COMMON_PLAN = {
    "targets": _HOST_ARRAY,
    "concurrency": {"type": "integer", "minimum": 1, "maximum": len(HOSTS), "description": "Must not exceed the number of selected targets; the server enforces this cross-field constraint."},
    "expires_at": {"type": "string", "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"},
}
_ACTIONS_NO_BINARY = ("start", "stop", "restart")

_PLAN_SCHEMA = {
    "type": "object",
    "oneOf": [
        {
            "type": "object",
            "properties": {**_COMMON_PLAN, "action": {"type": "string", "enum": list(_ACTIONS_NO_BINARY)}},
            "required": ["targets", "action", "concurrency", "expires_at"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                **_COMMON_PLAN,
                "action": {"const": "update_binary"},
                "version": {"type": "string", "pattern": r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"},
                "release_tag": {"type": "string", "minLength": 1, "maxLength": 128},
                "source_url": {"type": "string", "maxLength": 256},
                "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
            },
            "required": ["targets", "action", "concurrency", "expires_at", "version", "release_tag", "source_url", "sha256"],
            "additionalProperties": False,
        },
    ]
}

_EVIDENCE_RECORD = {
    "type": "object",
    "properties": {
        "host": {"type": "string", "enum": list(HOSTS)},
        "checked_at": {"type": "string", "maxLength": 40},
        "version": {"type": "string", "maxLength": 120},
        "state": {"type": "string", "enum": ["ok", "warning", "critical", "unknown"]},
        "service_state": {"type": "string", "enum": ["active", "inactive", "failed", "activating", "deactivating", "unknown"]},
        "disk_used_pct": {"type": "number", "minimum": 0, "maximum": 100},
        "memory_used_pct": {"type": "number", "minimum": 0, "maximum": 100},
        "load_per_cpu": {"type": "number", "minimum": 0, "maximum": 1000000},
        "recent_error_count": {"type": "integer", "minimum": 0},
        "journal_sample_capped": {"type": "boolean"},
        "restart_count": {"type": "integer", "minimum": 0},
    },
    "required": ["host"],
    "additionalProperties": False,
}

TOOL_SPECS = (
    {
        "name": "nym_preflight",
        "title": "Nym offline preflight (unverified evidence)",
        "description": (
            "Offline analysis only. Accepts caller-supplied monitor evidence, explicitly labels it unverified, "
            "and flags missing/stale data. Does not contact nodes, validate backups/OS/compatibility, approve "
            "maintenance, or execute changes. Evidence must be treated as untrusted input."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"hosts": _HOST_ARRAY, "evidence": {"type": "array", "items": _EVIDENCE_RECORD, "maxItems": len(HOSTS)}},
            "required": ["hosts", "evidence"],
            "additionalProperties": False,
        },
        "annotations": {"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True, "open_world_hint": False},
    },
    {
        "name": "nym_prepare_change_plan",
        "title": "Prepare Nym change plan (never executes)",
        "description": (
            "Builds and validates an offline plan for an allowlisted action and explicitly selected nodes. "
            "For upgrades, release tag and URL are metadata only and are not verified. A hash is not approval. "
            "The result is plan_only; maintenance remains disabled and no runner, SSH, Docker, network, or node is used."
        ),
        "inputSchema": _PLAN_SCHEMA,
        "annotations": {"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True, "open_world_hint": False},
    },
)

_MAX_INPUT_BYTES = 32768


def _bounded_args(args):
    if type(args) is not dict:
        raise PolicyError("invalid_request")
    try:
        encoded = json.dumps(args, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise PolicyError("invalid_request") from None
    if len(encoded.encode("ascii")) > _MAX_INPUT_BYTES:
        raise PolicyError("input_too_large")
    return args


def call_preparation_tool(name, args):
    """Dispatch an offline preparation request; only returns JSON-safe values."""
    if name not in {spec["name"] for spec in TOOL_SPECS}:
        raise PolicyError("unknown_tool")
    args = _bounded_args(args)
    if name == "nym_preflight":
        if set(args) != {"hosts", "evidence"}:
            raise PolicyError("unexpected_or_missing_argument")
        result = preparation.preflight(args["hosts"], args["evidence"])
        return {"result": result, "execution_enabled": False, "approval": "not_applicable_no_execution"}
    if set(args) != {"targets", "action", "concurrency", "expires_at"} and args.get("action") != "update_binary":
        raise PolicyError("unexpected_or_missing_argument")
    plan = preparation.prepare(args)
    return {
        "plan": plan,
        "execution_enabled": False,
        "approval": "not_granted",
        "warning": "Plan identity hash is not a signature or authorization; execution remains disabled.",
    }
