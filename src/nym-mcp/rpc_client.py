"""MCP-side client for the one permissioned controller Unix socket."""
from __future__ import annotations

import json
import socket
import struct

SOCKET_PATH = "/run/nym-controller.sock"
# This system-level ListenStream is created by systemd; SO_PEERCRED reports PID 1.
SYSTEMD_SOCKET_PEER = (1, 0, 0)
# Plan bytes are capped at 32768; 4096 bytes cover the JSON envelope/signature.
MAX_REQUEST_BYTES = 36864
MAX_RESPONSE_BYTES = 36864
RPC_TIMEOUT_SECONDS = 540


class RpcError(RuntimeError):
    """Safe transport error; never embeds a socket path or payload."""


def _encode(payload: dict) -> bytes:
    if type(payload) is not dict:
        raise RpcError("invalid_request")
    try:
        body = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise RpcError("invalid_request") from None
    if not body or len(body) > MAX_REQUEST_BYTES:
        raise RpcError("invalid_request")
    return struct.pack("!I", len(body)) + body


def _read_exact(connection: socket.socket, count: int) -> bytes:
    result = bytearray()
    while len(result) < count:
        data = connection.recv(count - len(result))
        if not data:
            raise RpcError("controller_unavailable")
        result.extend(data)
    return bytes(result)


def _decode_response(connection: socket.socket) -> dict:
    size = struct.unpack("!I", _read_exact(connection, 4))[0]
    if not 1 <= size <= MAX_RESPONSE_BYTES:
        raise RpcError("invalid_response")
    raw = _read_exact(connection, size)
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate")
            value[key] = item
        return value
    def constant(_value):
        raise ValueError("nonfinite")
    try:
        response = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise RpcError("invalid_response") from None
    if type(response) is not dict:
        raise RpcError("invalid_response")
    if response.get("ok") is True:
        if set(response) not in (
            {"ok", "exit_code", "output"},
            {"ok", "canonical_plan", "plan_digest", "maintenance_enabled", "execution"},
            {"ok", "approval_state", "operator", "operation", "hosts", "plan_digest", "expires_at", "maintenance_enabled", "execution"},
        ):
            raise RpcError("invalid_response")
        if "exit_code" in response:
            if type(response["exit_code"]) is not int or type(response["output"]) is not str:
                raise RpcError("invalid_response")
        elif "canonical_plan" in response:
            if (type(response["canonical_plan"]) is not str or
                    type(response["plan_digest"]) is not str or
                    response["maintenance_enabled"] is not False or
                    response["execution"] != "not_executed"):
                raise RpcError("invalid_response")
        elif (response["approval_state"] != "verified_one_time" or
              type(response["operator"]) is not str or type(response["operation"]) is not str or
              type(response["hosts"]) is not list or type(response["plan_digest"]) is not str or
              type(response["expires_at"]) is not int or response["maintenance_enabled"] is not False or
              response["execution"] != "not_executed"):
            raise RpcError("invalid_response")
    elif response.get("ok") is False:
        allowed_failures = (
            {"ok", "error"},
            {"ok", "error", "execution"},
            {"ok", "error", "execution", "runner_exit_code", "runner_output"},
            {"ok", "error", "execution", "authorization", "operation", "host", "plan_digest", "runner_exit_code", "runner_output"},
        )
        if set(response) not in allowed_failures or type(response["error"]) is not str:
            raise RpcError("invalid_response")
        if "execution" in response and response["execution"] != "not_executed":
            raise RpcError("invalid_response")
        if "runner_exit_code" in response and (type(response["runner_exit_code"]) is not int or type(response["runner_output"]) is not str):
            raise RpcError("invalid_response")
        if "authorization" in response and (
            response["authorization"] != "verified_one_time" or
            response["error"] != "maintenance_disabled" or
            response["operation"] not in ("restart", "update_binary", "restore") or
            type(response["host"]) is not str or
            type(response["plan_digest"]) is not str or
            len(response["plan_digest"]) != 64
        ):
            raise RpcError("invalid_response")
    else:
        raise RpcError("invalid_response")
    return response


def _peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise RpcError("peer_credentials_unavailable")
    raw = connection.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
    if len(raw) != struct.calcsize("3i"):
        raise RpcError("peer_credentials_unavailable")
    return struct.unpack("3i", raw)


def _request(
    payload: dict,
    *,
    path: str,
    expected_peer: tuple[int, int, int],
    timeout: float = RPC_TIMEOUT_SECONDS,
) -> dict:
    frame = _encode(payload)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout)
        connection.connect(path)
        if _peer_credentials(connection) != expected_peer:
            raise RpcError("controller_identity_mismatch")
        connection.sendall(frame)
        return _decode_response(connection)
    except (OSError, TimeoutError):
        raise RpcError("controller_unavailable") from None
    finally:
        connection.close()


def request(payload: dict) -> dict:
    """Use the fixed socket; systemd creates it and the controller authenticates MCP."""
    return _request(
        payload,
        path=SOCKET_PATH,
        expected_peer=SYSTEMD_SOCKET_PEER,
    )
