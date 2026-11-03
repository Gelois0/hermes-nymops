# ARCHITECTURE.md — NYMOPS Alpha

## Data path

```
Hermes Gateway (MCP stdio)
  -> sudo -n /usr/local/libexec/nym-mcp/nym-mcp-launcher
    -> systemd-run as user nym-mcp (PrivateNetwork, no docker.sock)
      -> mcp_server.py (tools)
        -> Unix socket RPC /run/nym-controller.sock
          -> nym-controller.service (LoadCredential ssh_key/known_hosts[/approval_secret])
            -> run-nym-ansible.sh (fixed argv)
              -> ephemeral Ansible container via Docker proxy
                -> SSH to allowlisted nodes
```

## Components

| Piece | Role |
|---|---|
| `nym-mcp` | MCP tool surface; preparation/preflight; no direct SSH |
| `nym-controller` | Validates RPC, audits, executes fixed runner |
| `nym-ansible` playbooks | Read collectors + gated restart/update |
| `human_approval` | HMAC approval records |
| `change_policy` / `preparation` | Plan construction; `apply()` gate |

## Docker boundary

- Hermes MCP process: **no** Docker socket
- Controller/runner: talks to an existing Docker **proxy** (not redesigned here)
- Image digest is pinned in `run-nym-ansible.sh`

## Playbooks

| Playbook | Class |
|---|---|
| `ping.yml` | connectivity |
| `monitor.yml` | read-only metrics/service |
| `context-diagnostic.yml` | read-only context |
| `journal_status.yml` | bounded journal status |
| `restart_nym_node.yml` | mutation (gated) |
| `update_nym_node.yml` | mutation (gated; URL+sha256 sealed) |

## Alpha limits

Live restart/update on production nodes remains **unvalidated** as a completed pilot. Architecture supports gated execution; operators must not infer permission from plan preparation alone.
