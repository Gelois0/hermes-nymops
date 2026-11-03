"""Configurable NYMOPS host allowlist. No free-form hosts outside this set."""
from __future__ import annotations

import os
import re

_DEFAULT = ("node-a", "node-b", "node-c", "node-d", "node-e", "node-f")
_HOST_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def allowed_hosts() -> tuple[str, ...]:
    raw = os.environ.get("NYM_ALLOWED_HOSTS", ",".join(_DEFAULT)).strip()
    if not raw:
        raise RuntimeError("NYM_ALLOWED_HOSTS empty")
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    if not 1 <= len(parts) <= 32:
        raise RuntimeError("NYM_ALLOWED_HOSTS size out of range")
    if len(set(parts)) != len(parts):
        raise RuntimeError("NYM_ALLOWED_HOSTS has duplicates")
    for host in parts:
        if not _HOST_TOKEN.fullmatch(host):
            raise RuntimeError("NYM_ALLOWED_HOSTS has invalid token")
    return parts


def allowed_host_set() -> frozenset[str]:
    return frozenset(allowed_hosts())
