"""Fixed read-only MCP-to-Ansible broker; no generic command execution."""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import socket
import stat
import struct
import sys
import time
from pathlib import Path
from typing import Callable

from controller_runtime import audit, execute, safe_output

from allowed_hosts import allowed_hosts
HOSTS = allowed_hosts()
BASE = Path(__file__).resolve().parent
RUNNER = BASE / "run-nym-ansible.sh"
MANIFEST = BASE / "manifest.sha256"
SOCKET_FD = 3
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 16384
FRAME_TIMEOUT_SECONDS = 5
CLIENT_TIMEOUT_SECONDS = 540
RUN_TIMEOUT_SECONDS = 480
OUTPUT_LIMIT_BYTES = 65536


class RequestError(ValueError):
    """Closed RPC-policy error; never includes caller-controlled text."""


def _duplicate_rejecting_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RequestError("invalid_request")
        result[key] = value
    return result


def _reject_constant(_value):
    raise RequestError("invalid_request")


def decode_request(data: bytes) -> dict:
    if type(data) is not bytes or not data or len(data) > MAX_REQUEST_BYTES:
        raise RequestError("invalid_request")
    try:
        request = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_duplicate_rejecting_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
        raise RequestError("invalid_request") from None
    if type(request) is not dict:
        raise RequestError("invalid_request")
    return request


def validate_request(request: dict) -> tuple[str, list[str] | None]:
    if type(request) is not dict or type(request.get("v")) is not int or request.get("v") != 1:
        raise RequestError("invalid_request")
    operation = request.get("op")
    if operation == "ping" and set(request) == {"v", "op"}:
        return "ping", None
    read_ops = {"monitor", "context_diagnostic", "journal_status"}
    mutation_ops = {"restart_nym_node", "update_nym_node"}
    if type(operation) is not str or operation not in read_ops | mutation_ops or set(request) != {"v", "op", "hosts"}:
        raise RequestError("invalid_request")
    hosts = request.get("hosts")
    if (
        type(hosts) is not list
        or not 1 <= len(hosts) <= len(HOSTS)
        or any(type(host) is not str or host not in HOSTS for host in hosts)
        or len(set(hosts)) != len(hosts)
    ):
        raise RequestError("invalid_request")
    if operation in mutation_ops and len(hosts) != 1:
        raise RequestError("invalid_request")
    return operation, list(hosts)


def encode_frame(response: dict) -> bytes:
    if type(response) is not dict:
        raise RequestError("invalid_response")
    try:
        body = json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise RequestError("invalid_response") from None
    if not body or len(body) > MAX_RESPONSE_BYTES:
        raise RequestError("invalid_response")
    return struct.pack("!I", len(body)) + body


def _recv_exact(connection: socket.socket, count: int) -> bytes:
    parts = bytearray()
    while len(parts) < count:
        chunk = connection.recv(count - len(parts))
        if not chunk:
            raise RequestError("invalid_request")
        parts.extend(chunk)
    return bytes(parts)


def read_frame(connection: socket.socket) -> dict:
    header = _recv_exact(connection, 4)
    length = struct.unpack("!I", header)[0]
    if not 1 <= length <= MAX_REQUEST_BYTES:
        raise RequestError("invalid_request")
    return decode_request(_recv_exact(connection, length))


def peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise RequestError("peer_credentials_unavailable")
    raw = connection.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
    if len(raw) != struct.calcsize("3i"):
        raise RequestError("peer_credentials_unavailable")
    return struct.unpack("3i", raw)


def build_runner_env(credentials_dir: str, state_dir: str, runtime_dir: str) -> dict[str, str]:
    paths = (credentials_dir, state_dir, runtime_dir)
    if any(type(path) is not str or not os.path.isabs(path) for path in paths):
        raise RequestError("controller_environment_invalid")
    # Explicit allowlist: never inherit SSH_AUTH_SOCK, DOCKER_HOST, or MCP env.
    return {
        "HOME": state_dir,
        "USER": "nym-controller",
        "LOGNAME": "nym-controller",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "CREDENTIALS_DIRECTORY": credentials_dir,
        "STATE_DIRECTORY": state_dir,
        "RUNTIME_DIRECTORY": runtime_dir,
    }


def _audit_path() -> Path:
    state_dir = os.environ.get("STATE_DIRECTORY", "")
    if not os.path.isabs(state_dir):
        raise RequestError("controller_environment_invalid")
    return Path(state_dir) / "audit.jsonl"


def run_operation(operation: str, hosts: list[str] | None) -> dict:
    if operation == "ping" and hosts is None:
        selected = list(HOSTS)
    elif operation in {"monitor", "context_diagnostic", "journal_status"} and hosts is not None:
        selected = list(hosts)
    elif operation in {"restart_nym_node", "update_nym_node"} and hosts is not None and len(hosts) == 1:
        selected = list(hosts)
    else:
        raise RequestError("invalid_request")

    credentials_dir = os.environ.get("CREDENTIALS_DIRECTORY", "")
    state_dir = os.environ.get("STATE_DIRECTORY", "")
    runtime_dir = os.environ.get("RUNTIME_DIRECTORY", "")
    env = dict(build_runner_env(credentials_dir, state_dir, runtime_dir))
    for key in (
        "NYM_ANSIBLE_BASE", "NYM_CONTROLLER_STATE", "NYM_STAGING_OFFLINE",
        "NYM_SSH_KEY", "NYM_KNOWN_HOSTS",
    ):
        if key in os.environ:
            env[key] = os.environ[key]
    if operation in {"restart_nym_node", "update_nym_node"}:
        # Mutations only when explicitly armed by gated apply(); never by MCP read path.
        if os.environ.get("NYM_ALLOW_MUTATION") != "1":
            audit(Path(state_dir) / "audit.jsonl", "rejected", selected, 77, 0, False)
            return {"ok": False, "error": "mutation_not_allowed"}
        env["NYM_ALLOW_MUTATION"] = "1"
        for key in (
            "NYM_UPDATE_VERSION", "NYM_UPDATE_TAG", "NYM_UPDATE_URL", "NYM_UPDATE_SHA256",
        ):
            if key in os.environ:
                env[key] = os.environ[key]
    audit_path = Path(state_dir) / "audit.jsonl"
    started = time.monotonic()
    audit(audit_path, operation, selected, -1, 0, False)
    code, output, truncated = 127, "", False
    try:
        timeout = 240 if operation == "context_diagnostic" else RUN_TIMEOUT_SECONDS
        if operation == "journal_status":
            timeout = 300
        code, output, truncated = execute(
            operation,
            selected,
            env=env,
            timeout=timeout,
            limit=OUTPUT_LIMIT_BYTES,
        )
        audit(audit_path, operation, selected, code, int((time.monotonic() - started) * 1000), truncated)
    except Exception:
        # Fail closed and do not return exception text, arguments, paths, or environment.
        return {"ok": False, "error": "controller_failure"}

    suffix = "\n[output truncated]" if truncated else ""
    if code == 124:
        suffix += "\nTimed out; process cleanup ran, remote cancellation is not proven."
    if code == 75:
        suffix += "\nController busy; no new operation started."
    safe = safe_output(output) + suffix
    return {"ok": True, "exit_code": int(code), "output": safe[:12000]}


def _send_response(connection: socket.socket, response: dict) -> None:
    connection.sendall(encode_frame(response))


def serve_connection(
    connection: socket.socket,
    expected_mcp_uid: int,
    runner: Callable[[str, list[str] | None], dict] = run_operation,
    audit_path: Path | None = None,
) -> None:
    connection.settimeout(FRAME_TIMEOUT_SECONDS)
    try:
        _pid, peer_uid, _gid = peer_credentials(connection)
        if peer_uid != expected_mcp_uid:
            if audit_path is not None:
                audit(audit_path, "rejected", [], 77, 0, False)
            _send_response(connection, {"ok": False, "error": "unauthorized_peer"})
            return
        request = read_frame(connection)
        operation, hosts = validate_request(request)
    except RequestError as exc:
        if audit_path is not None:
            try:
                audit(audit_path, "rejected", [], 77, 0, False)
            except Exception:
                pass
        error = exc.args[0] if exc.args and exc.args[0] in {
            "peer_credentials_unavailable", "invalid_request"
        } else "invalid_request"
        try:
            _send_response(connection, {"ok": False, "error": error})
        except OSError:
            pass
        return
    except OSError:
        return

    connection.settimeout(CLIENT_TIMEOUT_SECONDS)
    try:
        response = runner(operation, hosts)
        if type(response) is not dict or set(response) not in (
            {"ok", "exit_code", "output"}, {"ok", "error"}
        ):
            response = {"ok": False, "error": "controller_failure"}
        _send_response(connection, response)
    except Exception:
        try:
            _send_response(connection, {"ok": False, "error": "controller_failure"})
        except OSError:
            return


def verify_manifest(expected_uid: int = 0) -> None:
    """Fail closed if any deployed executable or input differs from root manifest."""
    st = MANIFEST.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != expected_uid or stat.S_IMODE(st.st_mode) & 0o022:
        raise PermissionError("unsafe manifest")
    lines = MANIFEST.read_text(encoding="ascii").splitlines()
    if not lines:
        raise PermissionError("empty manifest")
    seen = set()
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._/-]+)", line)
        if match is None:
            raise PermissionError("invalid manifest")
        digest, relative = match.groups()
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or relative in seen:
            raise PermissionError("invalid manifest path")
        seen.add(relative)
        target = BASE / path
        file_stat = target.lstat()
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != expected_uid
            or stat.S_IMODE(file_stat.st_mode) & 0o022
        ):
            raise PermissionError("unsafe deployed file")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            raise PermissionError("deployed file hash mismatch")
    required = {
        "controller_service.py", "controller_runtime.py", "human_approval.py", "approval_store.py",
        "run-nym-ansible.sh",
        "inventory.ini", "ansible.cfg", "ping.yml", "monitor.yml",
        "journal_status.yml", "restart_nym_node.yml", "update_nym_node.yml",
        "requirements-ansible-py313.in", "requirements-ansible-py313.lock", "context-diagnostic.yml",
    }
    if not required.issubset(seen):
        raise PermissionError("manifest incomplete")


def systemd_listener() -> socket.socket:
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise RuntimeError("systemd socket activation required")
    listener = socket.socket(fileno=SOCKET_FD)
    if listener.family != socket.AF_UNIX or listener.type & socket.SOCK_STREAM != socket.SOCK_STREAM:
        raise RuntimeError("unexpected systemd listener")
    listener.set_inheritable(False)
    return listener


def serve_forever(listener: socket.socket | None = None) -> None:
    if listener is None:
        listener = systemd_listener()
    mcp_uid = pwd.getpwnam("nym-mcp").pw_uid
    if os.getuid() != pwd.getpwnam("nym-controller").pw_uid:
        raise PermissionError("wrong controller identity")
    audit_path = _audit_path()
    while True:
        connection, _ = listener.accept()
        try:
            serve_connection(connection, mcp_uid, audit_path=audit_path)
        finally:
            connection.close()


def main() -> int:
    try:
        verify_manifest()
        serve_forever()
    except Exception:
        sys.stderr.write("nym-controller stopped: integrity, identity, credential, or socket check failed.\n")
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
