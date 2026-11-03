# TROUBLESHOOTING.md — NYMOPS Alpha

| Symptom | Likely cause | What to check |
|---|---|---|
| MCP launcher fails under sudo | Missing sudoers / wrong path | `deploy/sudoers/nym-mcp-launcher`; launcher mode `755 root:root` |
| `invalid_request` from controller | Host not in allowlist / bad payload | `NYM_ALLOWED_HOSTS`, inventory names, tool args |
| Runner exit `77` | Mutation without arming | Expected unless gated `apply()` set `NYM_ALLOW_MUTATION=1` |
| Runner exit `79` | `NYM_STAGING_OFFLINE=1` | Unset for live; keep for tests |
| Runner exit `73` | State/socket permissions | `StateDirectory`/`RuntimeDirectory` modes; agent sock ownership |
| SSH UNREACHABLE | Key/known_hosts/ACL on agent sock | LoadCredential mapping; never copy keys into the container image |
| `apply()` → `maintenance_disabled` | Default safe state | Only enable maintenance with explicit human process |
| Controller exit `70` | Manifest mismatch after file replace | Regenerate `manifest.sha256` for libexec contents |
| Offline tests fail missing `inventory.ini` | Inventory not present | Copy from `inventory.ini.example` |
| Hermes tools missing | Gateway stanza not loaded | Hermes version-specific MCP config; restart/reload per your Hermes docs |

Still blocked after checks: gather **sanitized** logs (redact IPs, keys, MACs) before sharing.
