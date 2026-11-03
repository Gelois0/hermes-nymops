# CONFIGURATION.md — NYMOPS Alpha

## Host allowlist

All MCP/controller/runner paths reject hosts outside the allowlist.

```bash
export NYM_ALLOWED_HOSTS=node-a,node-b,node-c
```

Rules:

- Tokens: `^[a-z][a-z0-9_-]{0,31}$`
- Unique, comma-separated, no trailing comma
- Must match Ansible inventory names and SSH `Host` aliases

Default example tokens: `node-a` … `node-f`.

## Inventory

```bash
cp src/nym-ansible/inventory.ini.example src/nym-ansible/inventory.ini
```

Use SSH host aliases (MagicDNS / short names). Prefer **not** embedding Tailscale `100.x` or public IPs in committed files. Keep a private `inventory.local.ini` if needed (gitignored pattern provided).

## SSH / Tailscale

- Dedicated operator key for node access
- `StrictHostKeyChecking=yes` and a maintained `known_hosts`
- Feed key + known_hosts through systemd `LoadCredential` (`ssh_key`, `known_hosts`)
- Sample unit paths: `/etc/nym-controller/ssh_key`, `/etc/nym-controller/known_hosts`

Do not place private keys in the repository or shared chat folders.

## Approval secret (HMAC)

| Item | Guidance |
|---|---|
| Generate | `openssl rand -out /etc/nym-controller/approval_secret 48` |
| Mode | `600 root:root` |
| Delivery | `LoadCredential=approval_secret:…` drop-in |
| Env fallback | `NYM_APPROVAL_SECRET` (discouraged vs LoadCredential) |
| Binding | plan id, content hash, action, targets, expiry, single-use MAC when `allows_apply=true` |

## Maintenance flags

| Variable | Default | Meaning |
|---|---|---|
| `NYM_MAINTENANCE_ENABLED` | unset/`0` | `apply()` blocked (`maintenance_disabled`) |
| `NYM_ALLOW_MUTATION` | unset | Runner rejects restart/update (exit `77`) unless armed by gated apply |
| `NYM_STAGING_OFFLINE` | unset | Test-only; refuses live Docker/Ansible |

## Docker

The fixed runner expects an existing Docker API endpoint (reference: `tcp://127.0.0.1:2376` in runner). Configure your environment to use a **proxy**, not a raw `docker.sock` mount into Hermes.

## Hermes MCP example

See `examples/hermes-mcp-nym-ansible.yaml.example`. Adjust to your Gateway schema; do not copy production hostnames or credentials into examples.
