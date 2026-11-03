# CONTRIBUTING.md — NYMOPS Alpha

## Ground rules

- Keep `maintenance_disabled` by default.
- Do not add free-form command execution to MCP.
- Do not weaken host/playbook allowlists for convenience.
- Do not commit secrets, real inventories, or production evidence dumps.
- Prefer offline tests for every policy/runner change.

## Dev loop

```bash
python3 tests/test_m3_m6_offline.py
python3 tests/test_human_approval_offline.py
bash -n src/nym-ansible/run-nym-ansible.sh
```

## PRs

- Describe threat-model impact.
- Mark any live-node testing as requiring explicit operator approval.
- Do not request maintainers to run `apply()` or enable maintenance in CI.

## Agent notes

Optional Cursor agent role cards live under `agents/`. They are workflow helpers, not runtime components.
