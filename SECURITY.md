# SECURITY.md — NYMOPS Alpha

## Model

NYMOPS assumes a hostile or mistake-prone automation client (LLM agent) and therefore:

1. **Fixed operations only** — no free-form shell from MCP.
2. **Allowlisted hosts** — `NYM_ALLOWED_HOSTS`.
3. **Allowlisted playbooks** — ping/monitor/context/journal/restart/update only.
4. **Default deny maintenance** — `apply()` → `maintenance_disabled`.
5. **Human approval** — HMAC-bound, single-use, content-hashed plans for mutating apply.
6. **Audit before mutate** — approval metadata must be recorded.
7. **Credential isolation** — systemd `LoadCredential`; MCP launcher uses `PrivateNetwork` and blocks docker/SSH agent paths.
8. **No docker.sock in Hermes** — runner uses a constrained Docker API endpoint.

## What this does *not* guarantee

- Compatibility with every Hermes Agent build
- Safety if you disable allowlists, mount docker.sock, or ship real secrets in git
- That restart/update playbooks are production-proven (live pilots still incomplete)
- Secrecy of node topology if you publish real inventories or evidence dumps

## Publishing / supply-chain hygiene

Never commit:

- SSH private keys, `approval_secret`, OAuth/GitHub tokens, `.env` with secrets
- Real Tailscale/public IPs, production hostnames that identify your fleet
- Logs/evidence JSON with addresses, identity keys, or credential paths
- `*.bak-*` backups

Rotate any secret that may have been exposed outside this private packaging workflow.

## Incident note (packaging audit)

During packaging prep, local workspace evidence and operator docs contained production identifiers (node aliases, public IPs, Tailscale addresses, identity keys). Those artifacts are **excluded** from the publish tree. No claim is made that secrets were previously pushed to GitHub for this project (workspace had **no** git history at packaging time).
