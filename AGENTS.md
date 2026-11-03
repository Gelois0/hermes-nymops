# AGENTS.md — NYMOPS Alpha operator notes

This repository is **experimental (`v0.1.0-alpha`)**.

## Hard rules for automation agents

- Do not restart or update Nym nodes without a fresh, explicit human approval for that exact action.
- Do not call `apply()` or set `NYM_MAINTENANCE_ENABLED=1` unless the operator explicitly orders it for a named plan.
- Do not mount or request `docker.sock` inside Hermes.
- Do not print secrets, private keys, approval MACs, or raw credential files.
- Do not contact hosts outside `NYM_ALLOWED_HOSTS`.
- Prefer offline tests and dry-runs before any live read.

## Verified vs unproven

- Read-only monitoring and offline gating tests: see README status table.
- Live restart/update: **not** proven complete.
- Fresh install reproducibility on arbitrary Hermes hosts: **not** claimed.
