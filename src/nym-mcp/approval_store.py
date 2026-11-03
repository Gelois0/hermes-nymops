"""Persistent single-use approval consumption (fail-closed)."""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from pathlib import Path

_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class ApprovalStoreError(ValueError):
    pass


def _store_path() -> Path:
    state = os.environ.get("STATE_DIRECTORY", "")
    if not os.path.isabs(state):
        raise ApprovalStoreError("unsafe_state_directory")
    return Path(state) / "consumed_approvals.jsonl"


def load_consumed(path: Path | None = None) -> set[str]:
    path = path or _store_path()
    if not path.exists():
        return set()
    try:
        st = path.lstat()
    except OSError as exc:
        raise ApprovalStoreError("unsafe_store") from exc
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise ApprovalStoreError("unsafe_store")
    consumed: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ApprovalStoreError("corrupt_store") from exc
        aid = obj.get("approval_id")
        if type(aid) is not str or _ID_RE.fullmatch(aid) is None:
            raise ApprovalStoreError("corrupt_store")
        consumed.add(aid)
    return consumed


def record_consumed(approval_id: str, *, path: Path | None = None, plan_id: str = "") -> None:
    if type(approval_id) is not str or _ID_RE.fullmatch(approval_id) is None:
        raise ApprovalStoreError("invalid_approval_id")
    if plan_id and (type(plan_id) is not str or _ID_RE.fullmatch(plan_id) is None):
        raise ApprovalStoreError("invalid_plan_id")
    path = path or _store_path()
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    pstat = parent.lstat()
    if not stat.S_ISDIR(pstat.st_mode) or pstat.st_uid != os.geteuid() or stat.S_IMODE(pstat.st_mode) & 0o077:
        raise ApprovalStoreError("unsafe_state_directory")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        current = os.fstat(fd)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(current.st_mode) & 0o077
        ):
            raise ApprovalStoreError("unsafe_store")
        already = load_consumed(path)
        if approval_id in already:
            raise ApprovalStoreError("approval_already_consumed")
        record = {"approval_id": approval_id}
        if plan_id:
            record["plan_id"] = plan_id
        data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        fcntl.flock(fd, fcntl.LOCK_EX)
        if os.write(fd, data) != len(data):
            raise ApprovalStoreError("store_write_failed")
        os.fsync(fd)
    finally:
        os.close(fd)
