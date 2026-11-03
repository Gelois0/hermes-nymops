# NYMOPS agent team (Cursor)

Cycle: **DIAGNOSTIC → REPAIR → VALIDATOR → RESULT** (≤3 attempts per problem).

| Role | File | May modify |
|---|---|---|
| NYMOPS-DIAGNOSTIC | `agents/NYMOPS-DIAGNOSTIC.md` | Nothing (read-only) |
| NYMOPS-REPAIR | `agents/NYMOPS-REPAIR.md` | Staging/workspace only |
| NYMOPS-VALIDATOR | `agents/NYMOPS-VALIDATOR.md` | Docs/evidence only |

Coordinator: main Cursor agent. No concurrent edits to the same files. Human auth required for VPS deploy and any node mutation.
