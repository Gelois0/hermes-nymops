# INSTALL.md — NYMOPS Alpha

**Alpha software.** This guide documents the intended install path based on the existing architecture. A full greenfield install on a third-party Hermes Agent has **not** been proven in this packaging run. Treat every live step as approval-gated.

## 0. Prerequisites (check)

| Dependency | Notes |
|---|---|
| Linux host for Hermes/controller | systemd 250+ recommended |
| Python | Package historically validated against **CPython 3.13.5** for MCP venv; confirm your Hermes/runtime match before install |
| Docker | Existing proxy for the Ansible runner container (default reference `tcp://127.0.0.1:2376`). **Do not** mount `/var/run/docker.sock` into Hermes |
| Ansible | Pulled inside the pinned runner image on each call (hash-locked requirements in `src/nym-controller/requirements-ansible-py313.lock`) |
| MCP Python package | Hash-locked in `src/nym-mcp/requirements-mcp-py313.lock` (pin historically `mcp==2.3.0`) |
| SSH/Tailscale | Operator key + `known_hosts` with **strict** host-key verification |
| Hermes Agent | MCP stdio integration via fixed sudo launcher (version-specific; not universally guaranteed) |

```bash
command -v python3.13 systemctl docker sha256sum openssl
python3.13 --version
systemctl --version | head -1
```

## 1. Offline validation (required before install)

From the repository root:

```bash
bash -n src/nym-ansible/run-nym-ansible.sh
python3 -m py_compile src/nym-mcp/*.py src/nym-controller/*.py
python3 tests/test_m3_m6_offline.py
python3 tests/test_human_approval_offline.py
```

Expect offline suites to report success with **zero** failures. These tests do not contact nodes.

## 2. Configure your nodes

```bash
cp src/nym-ansible/inventory.ini.example src/nym-ansible/inventory.ini
# edit inventory.ini + export NYM_ALLOWED_HOSTS=node-a,node-b,...
```

See [CONFIGURATION.md](CONFIGURATION.md). Host tokens must match SSH `Host` aliases and the allowlist.

## 3. Stage credentials (never commit)

Prepare root-readable credential **sources** for systemd `LoadCredential` (example paths used by the sample unit):

- `/etc/nym-controller/ssh_key` (private key, mode `600`)
- `/etc/nym-controller/known_hosts` (strict known hosts)
- Later: `/etc/nym-controller/approval_secret` (`openssl rand -out … 48`, mode `600`)

Do not copy private keys into this git tree.

## 4. Install files (example layout)

Target layout mirrors the existing architecture:

| Source | Destination |
|---|---|
| `src/nym-mcp/*` | `/usr/local/libexec/nym-mcp/` |
| `deploy/nym-mcp-launcher` | `/usr/local/libexec/nym-mcp/nym-mcp-launcher` |
| `src/nym-controller/*` + playbooks/runner | `/usr/local/libexec/nym-controller/` |
| `deploy/systemd/nym-controller.service` | `/etc/systemd/system/nym-controller.service` |
| `deploy/systemd/nym-controller.socket` | `/etc/systemd/system/nym-controller.socket` |
| `deploy/sysusers/nymops.conf` | `/usr/lib/sysusers.d/nymops.conf` |

Use `deploy/install-nymops.sh --check` (read-only) then `--install` **only after explicit approval** on your host. The script refuses to overwrite an existing NYMOPS install.

Create the MCP venv under `/opt/nym-mcp/venv` from `requirements-mcp-py313.lock` with hash checking when your network policy allows.

## 5. systemd and minimal permissions

```bash
systemd-sysusers /usr/lib/sysusers.d/nymops.conf
systemd-analyze verify /etc/systemd/system/nym-controller.service /etc/systemd/system/nym-controller.socket
systemctl daemon-reload
# enable/start only after separate approval
```

Optional sudoers fragment (review before install): `deploy/sudoers/nym-mcp-launcher` — argument-free launcher only.

## 6. Generate per-install approval secret

```bash
sudo openssl rand -out /etc/nym-controller/approval_secret 48
sudo chmod 600 /etc/nym-controller/approval_secret
# install drop-in from deploy/systemd/nym-controller-approval-secret.conf.example
sudo systemctl daemon-reload
# restart controller only with explicit approval
```

Never commit the secret. Prefer `LoadCredential` over environment variables.

## 7. Hermes MCP wiring

Add a Gateway MCP stanza similar to `examples/hermes-mcp-nym-ansible.yaml.example`. Exact schema fields depend on your Hermes version. Start with read-only tools.

## 8. Read-only validation

After services are approved to start:

1. MCP `tools/list` shows expected tools.
2. Monitor/journal against one allowlisted host (explicit approval).
3. Confirm `apply()` still returns `maintenance_disabled` while `NYM_MAINTENANCE_ENABLED` is unset.
4. Keep real restart/update disabled until you deliberately mint a valid HMAC approval.

## 9. Defaults to keep

- `NYM_MAINTENANCE_ENABLED=0`
- No `NYM_ALLOW_MUTATION` outside gated `apply()`
- No `docker.sock` in Hermes
- No production claim until your own install evidence is recorded
