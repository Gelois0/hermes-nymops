from __future__ import annotations

import anyio
import json
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from change_policy import PolicyError
from preparation_mcp import TOOL_SPECS as PREPARATION_TOOL_SPECS, call_preparation_tool
from rpc_client import RpcError, request as controller_request

from allowed_hosts import allowed_hosts

TOOL_NAME = "run_nym_connectivity_check"
HOSTS = allowed_hosts()
PREPARATION_NAMES = frozenset(spec["name"] for spec in PREPARATION_TOOL_SPECS)


def _rpc(hosts=None) -> dict:
    if hosts is None:
        payload = {"v": 1, "op": "ping"}
    else:
        if (
            type(hosts) is not list
            or not 1 <= len(hosts) <= len(HOSTS)
            or any(type(host) is not str or host not in HOSTS for host in hosts)
            or len(set(hosts)) != len(hosts)
        ):
            raise RpcError("invalid_request")
        payload = {"v": 1, "op": "monitor", "hosts": hosts}
    return controller_request(payload)


def _context_rpc(hosts: list[str]) -> dict:
    if (
        type(hosts) is not list
        or not 1 <= len(hosts) <= len(HOSTS)
        or any(type(host) is not str or host not in HOSTS for host in hosts)
        or len(set(hosts)) != len(hosts)
    ):
        raise RpcError("invalid_request")
    return controller_request({"v": 1, "op": "context_diagnostic", "hosts": hosts})


def _journal_rpc(hosts: list[str]) -> dict:
    if (
        type(hosts) is not list
        or not 1 <= len(hosts) <= len(HOSTS)
        or any(type(host) is not str or host not in HOSTS for host in hosts)
        or len(set(hosts)) != len(hosts)
    ):
        raise RpcError("invalid_request")
    return controller_request({"v": 1, "op": "journal_status", "hosts": hosts})


async def _list_tools(_context, _params) -> ListToolsResult:
    tool = Tool(
        name=TOOL_NAME,
        title="Nym connectivity check (Ansible ping)",
        description=(
            "Runs only the fixed Ansible connectivity ping against the six verified Nym nodes. "
            "No updates or service/configuration changes. Ansible temporarily transfers its ping module "
            "and cleans it up. The MCP has no Docker, TCP proxy, SSH credential, or agent access."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    monitor = Tool(
        name="nym_monitor",
        title="Nym node read-only monitoring",
        description=(
            "Read-only on-demand Nym and resource monitoring for explicitly selected verified hosts. "
            "No repairs. Ansible uses temporary remote files and removes them."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "hosts": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(HOSTS)},
                    "minItems": 1,
                    "maxItems": len(HOSTS),
                    "uniqueItems": True,
                }
            },
            "required": ["hosts"],
            "additionalProperties": False,
        },
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    context_diagnostic = Tool(
        name="nym_context_diagnostic",
        title="Nym node context metadata diagnostic",
        description=(
            "Read-only metadata discovery for allowlisted Nym nodes. Requires remote UID 0 before systemd queries; "
            "nodes are processed sequentially. Excludes ExecStart, Environment values, credentials, and file contents."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "hosts": {"type": "array", "items": {"type": "string", "enum": list(HOSTS)},
                          "minItems": 1, "maxItems": len(HOSTS), "uniqueItems": True}
            },
            "required": ["hosts"],
            "additionalProperties": False,
        },
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    )
    preparation_tools = [
        Tool(
            name=spec["name"],
            title=spec["title"],
            description=spec["description"],
            inputSchema=spec["inputSchema"],
            annotations=ToolAnnotations(**spec["annotations"]),
        )
        for spec in PREPARATION_TOOL_SPECS
    ]
    journal_status = Tool(
        name="nym_journal_status",
        title="Nym node journal status (capped counts)",
        description=(
            "Read-only capped journal/priority counts and service state for allowlisted hosts. "
            "Never returns MESSAGE bodies. No repairs or service changes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "hosts": {"type": "array", "items": {"type": "string", "enum": list(HOSTS)},
                          "minItems": 1, "maxItems": len(HOSTS), "uniqueItems": True}
            },
            "required": ["hosts"],
            "additionalProperties": False,
        },
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    )
    return ListToolsResult(tools=[tool, monitor, context_diagnostic, journal_status, *preparation_tools])


async def _call_tool(_context, request) -> CallToolResult:
    if request.name in PREPARATION_NAMES:
        try:
            result = call_preparation_tool(request.name, request.arguments or {})
            text = json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True)
        except PolicyError as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], str) else "preparation_rejected"
            if not code.replace("_", "").isalnum():
                code = "preparation_rejected"
            return CallToolResult(content=[TextContent(text=f"Preparation rejected: {code}")], isError=True)
        except Exception:
            return CallToolResult(content=[TextContent(text="Preparation failed safely.")], isError=True)
        return CallToolResult(content=[TextContent(text=text)], isError=False)

    if request.name == "nym_monitor":
        args = request.arguments or {}
        if type(args) is not dict or set(args) != {"hosts"}:
            return CallToolResult(content=[TextContent(text="Rejected: hosts object required.")], isError=True)
        hosts = args.get("hosts")
        try:
            result = await anyio.to_thread.run_sync(_rpc, hosts)
        except RpcError as exc:
            return CallToolResult(content=[TextContent(text=f"Controller RPC failed: {exc.args[0]}" )], isError=True)
        return CallToolResult(
            content=[TextContent(text=f"Runner exit code: {result['exit_code']}\n{result['output']}")],
            isError=result["exit_code"] != 0,
        )

    if request.name == "nym_journal_status":
        args = request.arguments or {}
        if type(args) is not dict or set(args) != {"hosts"}:
            return CallToolResult(content=[TextContent(text="Rejected: hosts object required.")], isError=True)
        try:
            result = await anyio.to_thread.run_sync(_journal_rpc, args["hosts"])
        except RpcError as exc:
            return CallToolResult(content=[TextContent(text=f"Controller RPC failed: {exc.args[0]}")], isError=True)
        return CallToolResult(
            content=[TextContent(text=f"Runner exit code: {result['exit_code']}\n{result['output']}")],
            isError=result["exit_code"] != 0,
        )

    if request.name == "nym_context_diagnostic":
        args = request.arguments or {}
        if type(args) is not dict or set(args) != {"hosts"}:
            return CallToolResult(content=[TextContent(text="Rejected: hosts object required.")], isError=True)
        try:
            result = await anyio.to_thread.run_sync(_context_rpc, args["hosts"])
        except RpcError as exc:
            return CallToolResult(content=[TextContent(text=f"Controller RPC failed: {exc.args[0]}")], isError=True)
        return CallToolResult(
            content=[TextContent(text=f"Runner exit code: {result['exit_code']}\\n{result['output']}")],
            isError=result["exit_code"] != 0,
        )

    if request.name != TOOL_NAME:
        return CallToolResult(content=[TextContent(text="Unknown tool.")], isError=True)
    if request.arguments not in (None, {}):
        return CallToolResult(content=[TextContent(text="This fixed operation accepts no arguments.")], isError=True)
    try:
        result = await anyio.to_thread.run_sync(_rpc)
    except RpcError as exc:
        return CallToolResult(content=[TextContent(text=f"Controller RPC failed: {exc.args[0]}")], isError=True)
    return CallToolResult(
        content=[TextContent(text=f"Runner exit code: {result['exit_code']}\n{result['output']}")],
        isError=result["exit_code"] != 0,
    )


server = Server(
    "nym-ansible-approval-bridge",
    version="1.0.0",
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
)


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(_main, backend="asyncio")
