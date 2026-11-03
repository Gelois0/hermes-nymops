# NYMOPS — Alpha

**Experimental** Nym node management for [Hermes Agent](https://github.com/) via MCP, a fixed controller, Ansible, and SSH/Tailscale.

> **Status:** `v0.1.0-alpha` — software experimental en desarrollo.  
> **No** es una versión estable ni está lista para producción.  
> Use only in controlled environments. Real restart/update execution has **not** been validated as complete end-to-end pilots.

## What it does

NYMOPS lets a Hermes Agent manage a small allowlisted set of Nym nodes through a constrained path:

`Hermes → nym-ansible MCP → controller (Unix socket) → fixed Ansible runner → SSH → nodes`

Verified capability classes (see table below) include read-only monitoring, diagnostics, plan preparation, HMAC human approval, audit hooks, and hard blocks on unauthorized maintenance. `maintenance_disabled` / `apply()` remain the default.

## Feature status

| Area | Status |
|---|---|
| MCP tools surface (monitor, ping/connectivity, context, journal, preflight, prepare plan) | Verified in the author's Hermes VPS install (read-only / planning paths) |
| Offline unit tests (approval HMAC, gating, runner dry-run) | Verified offline in this package |
| `apply()` blocked unless maintenance explicitly enabled | Verified offline + in author's install |
| HMAC approval + single-use consume | Verified offline; secret install validated in author's install |
| Restart / update **capability** (plan + gates, no live mutation) | Validated without real execution |
| Restart / update **live** on real nodes | **Not validated** — do not treat as production-ready |
| Fresh install on a different Hermes Agent | **Not claimed reproducible yet** — docs prepared; full greenfield install not proven here |
| Universal Hermes compatibility | **Not promised** |

The author's historical “6/6 PASS” monitor checks are **not** a certification of complete production readiness.

## Safety defaults

- `NYM_MAINTENANCE_ENABLED` unset/`0` → `apply()` raises `maintenance_disabled`
- Mutations require human approval (HMAC), audit, and an armed mutation flag from gated `apply()`
- No automatic destructive operations
- Do not mount `docker.sock` into Hermes; use an existing constrained Docker proxy if your runner needs Docker
- Host allowlist via `NYM_ALLOWED_HOSTS` (default example: `node-a`…`node-f`)

## Repository layout

```
src/nym-mcp/          MCP server + preparation/policy
src/nym-controller/   Unix-socket controller + runtime + runner
src/nym-ansible/      Playbooks + fixed runner script
deploy/               systemd units, launcher, sudoers, sysusers
tests/                Offline suites (no nodes)
examples/             Hermes MCP stanza example
```

## Quick start (operators)

1. Read [SECURITY.md](SECURITY.md) and [INSTALL.md](INSTALL.md).
2. Configure nodes and SSH: [CONFIGURATION.md](CONFIGURATION.md).
3. Run offline tests before any live wiring.
4. Keep maintenance disabled until you deliberately approve a change.

```bash
python3 tests/test_m3_m6_offline.py
python3 tests/test_human_approval_offline.py
```

## Documentation

- [INSTALL.md](INSTALL.md) — step-by-step install
- [CONFIGURATION.md](CONFIGURATION.md) — nodes, SSH, secrets
- [ARCHITECTURE.md](ARCHITECTURE.md) — MCP / Docker / Ansible path
- [SECURITY.md](SECURITY.md) — threat model and controls
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [LICENSE](LICENSE) — MIT License

## License

License **not assigned** in this alpha packaging step. See `LICENSE` (MIT proposed).
