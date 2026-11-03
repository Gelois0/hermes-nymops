"""Fail-closed runtime for fixed Nym controller operations (staging M3–M6)."""
from __future__ import annotations

import fcntl
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from allowed_hosts import allowed_hosts
HOSTS = allowed_hosts()
READ_OPS = frozenset({"ping", "monitor", "context_diagnostic", "journal_status"})
MUTATION_OPS = frozenset({"restart_nym_node", "update_nym_node"})
AUDIT_OPS = READ_OPS | MUTATION_OPS | frozenset({"rejected", "approval_recorded", "apply_blocked"})
RUNNER = (Path(__file__).resolve().parent / "run-nym-ansible.sh").resolve()
_ALLOWED_ENV = {
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "CREDENTIALS_DIRECTORY",
    "STATE_DIRECTORY", "RUNTIME_DIRECTORY",
}
_STAGING_ENV = {
    "NYM_ANSIBLE_BASE", "NYM_CONTROLLER_STATE", "NYM_STAGING_OFFLINE",
    "NYM_SSH_KEY", "NYM_KNOWN_HOSTS",
}
_UPDATE_ENV = {
    "NYM_ALLOW_MUTATION", "NYM_UPDATE_VERSION", "NYM_UPDATE_TAG",
    "NYM_UPDATE_URL", "NYM_UPDATE_SHA256",
}
_SECRET_RE = re.compile(
    r"(?im)(\b(?:password|passwd|token|secret|private[_ -]?key|authorization|cookie|ssh_auth_sock)\b\s*[:=]\s*)[^\s,;]+"
)


class RuntimeErrorSafe(RuntimeError):
    """Safe fixed-operation runtime error with no caller-controlled details."""


def _validate_hosts(hosts: list[str] | None, *, allow_all_for_ping: bool = False) -> list[str]:
    if allow_all_for_ping and hosts == list(HOSTS):
        return list(HOSTS)
    if (
        type(hosts) is not list
        or not 1 <= len(hosts) <= len(HOSTS)
        or any(type(host) is not str or host not in HOSTS for host in hosts)
        or len(set(hosts)) != len(hosts)
    ):
        raise RuntimeErrorSafe("invalid_request")
    return list(hosts)


def _validate_operation(operation: str, hosts: list[str] | None) -> tuple[str, list[str]]:
    if operation == "ping" and hosts == list(HOSTS):
        return "ping", list(HOSTS)
    if type(operation) is str and operation in {"monitor", "context_diagnostic", "journal_status"}:
        return operation, _validate_hosts(hosts)
    if type(operation) is str and operation in MUTATION_OPS:
        selected = _validate_hosts(hosts)
        if len(selected) != 1:
            raise RuntimeErrorSafe("invalid_request")
        return operation, selected
    raise RuntimeErrorSafe("invalid_request")


def _validate_env(env: Mapping[str, str], *, mutation: bool = False) -> dict[str, str]:
    if type(env) is not dict:
        raise RuntimeErrorSafe("controller_environment_invalid")
    allowed = set(_ALLOWED_ENV) | set(_STAGING_ENV)
    if mutation:
        allowed |= _UPDATE_ENV
    if set(env) - allowed:
        raise RuntimeErrorSafe("controller_environment_invalid")
    if not _ALLOWED_ENV.issubset(set(env)):
        raise RuntimeErrorSafe("controller_environment_invalid")
    if any(type(k) is not str or type(v) is not str or "\x00" in v for k, v in env.items()):
        raise RuntimeErrorSafe("controller_environment_invalid")
    for key in ("HOME", "CREDENTIALS_DIRECTORY", "STATE_DIRECTORY", "RUNTIME_DIRECTORY"):
        if not os.path.isabs(env[key]):
            raise RuntimeErrorSafe("controller_environment_invalid")
    if env["USER"] != "nym-controller" or env["LOGNAME"] != "nym-controller":
        raise RuntimeErrorSafe("controller_environment_invalid")
    if mutation and env.get("NYM_ALLOW_MUTATION") != "1":
        raise RuntimeErrorSafe("mutation_not_allowed")
    return dict(env)


def execute(
    operation: str,
    hosts: list[str] | None,
    *,
    env: Mapping[str, str],
    timeout: int,
    limit: int,
) -> tuple[int, str, bool]:
    """Run only a fixed runner mode; never accepts free argv/cmd."""
    operation, selected = _validate_operation(operation, hosts)
    mutation = operation in MUTATION_OPS
    clean_env = _validate_env(env, mutation=mutation)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise RuntimeErrorSafe("invalid_runtime_limit")
    if operation == "context_diagnostic" and timeout > 240:
        raise RuntimeErrorSafe("invalid_runtime_limit")
    if type(limit) is not int or not 1 <= limit <= 1_048_576:
        raise RuntimeErrorSafe("invalid_runtime_limit")
    argv = ["/usr/bin/bash", str(RUNNER)]
    if operation == "monitor":
        argv.extend(["--monitor", ",".join(selected)])
    elif operation == "context_diagnostic":
        argv.extend(["--context-diagnostic", ",".join(selected)])
    elif operation == "journal_status":
        argv.extend(["--journal-status", ",".join(selected)])
    elif operation == "restart_nym_node":
        argv.extend(["--restart", selected[0]])
    elif operation == "update_nym_node":
        argv.extend(["--update-binary", selected[0]])
    return _run_bounded(argv, cwd=str(RUNNER.parent), env=clean_env, timeout=timeout, limit=limit)


def _run_bounded(
    argv: list[str], *, cwd: str, env: dict[str, str], timeout: int, limit: int
) -> tuple[int, str, bool]:
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        close_fds=True,
        start_new_session=True,
        bufsize=0,
    )
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    output = bytearray()
    truncated = False
    timed_out = False
    eof = False
    try:
        while not eof or proc.poll() is None:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0 and (proc.poll() is None or not eof):
                timed_out = True
                break
            events = selector.select(min(0.1, max(0.0, remaining))) if not eof else []
            for key, _mask in events:
                try:
                    chunk = os.read(key.fd, 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    eof = True
                    selector.unregister(key.fileobj)
                    continue
                free = max(0, limit - len(output))
                if free:
                    output.extend(chunk[:free])
                if len(chunk) > free:
                    truncated = True
            if proc.poll() is not None and not events and not eof:
                try:
                    chunk = os.read(fd, 8192)
                    if not chunk:
                        eof = True
                        selector.unregister(proc.stdout)
                    else:
                        free = max(0, limit - len(output))
                        if free:
                            output.extend(chunk[:free])
                        if len(chunk) > free:
                            truncated = True
                except BlockingIOError:
                    pass
        if timed_out:
            _terminate_process_group(proc)
            return 124, output.decode("utf-8", "replace"), truncated
        code = proc.wait(timeout=1)
        return int(code), output.decode("utf-8", "replace"), truncated
    except BaseException:
        _terminate_process_group(proc)
        raise
    finally:
        selector.close()
        proc.stdout.close()


def _terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


def safe_output(output: str) -> str:
    if type(output) is not str:
        raise RuntimeErrorSafe("invalid_output")
    return _SECRET_RE.sub(r"\1[REDACTED]", output)


def audit(
    path: Path,
    operation: str,
    hosts: list[str],
    exit_code: int,
    elapsed_ms: int,
    truncated: bool,
    *,
    extra: dict | None = None,
) -> None:
    """Append a metadata-only, symlink-safe JSONL record; fail closed on unsafe state."""
    if operation not in AUDIT_OPS:
        raise RuntimeErrorSafe("invalid_audit_record")
    if type(hosts) is not list or any(type(h) is not str or h not in HOSTS for h in hosts):
        raise RuntimeErrorSafe("invalid_audit_record")
    if type(exit_code) is not int or type(elapsed_ms) is not int or elapsed_ms < 0 or type(truncated) is not bool:
        raise RuntimeErrorSafe("invalid_audit_record")
    path = Path(path)
    state_dir = os.environ.get("STATE_DIRECTORY", "")
    if (
        not os.path.isabs(state_dir)
        or path != Path(state_dir) / "audit.jsonl"
        or path.name != "audit.jsonl"
        or not path.is_absolute()
    ):
        raise RuntimeErrorSafe("unsafe_audit_target")
    parent = path.parent
    try:
        pstat = parent.lstat()
    except OSError:
        raise RuntimeErrorSafe("unsafe_audit_directory") from None
    if not stat.S_ISDIR(pstat.st_mode) or pstat.st_uid != os.geteuid() or stat.S_IMODE(pstat.st_mode) & 0o077:
        raise RuntimeErrorSafe("unsafe_audit_directory")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        raise RuntimeErrorSafe("unsafe_audit_target") from None
    try:
        current = os.fstat(fd)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(current.st_mode) & 0o077
        ):
            raise RuntimeErrorSafe("unsafe_audit_file")
        record = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "hosts": hosts,
            "exit_code": exit_code,
            "elapsed_ms": elapsed_ms,
            "truncated": truncated,
        }
        if extra is not None:
            if type(extra) is not dict:
                raise RuntimeErrorSafe("invalid_audit_record")
            # Only allow fixed metadata keys — never raw plan bodies or secrets.
            for key in ("approval_id", "plan_id", "action", "reason"):
                if key in extra:
                    value = extra[key]
                    if type(value) is not str or len(value) > 128:
                        raise RuntimeErrorSafe("invalid_audit_record")
                    record[key] = value
        data = (json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        fcntl.flock(fd, fcntl.LOCK_EX)
        written = os.write(fd, data)
        if written != len(data):
            raise RuntimeErrorSafe("audit_write_failed")
    except OSError:
        raise RuntimeErrorSafe("audit_write_failed") from None
    finally:
        os.close(fd)
