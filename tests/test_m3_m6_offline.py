#!/usr/bin/env python3
"""Offline suite for staging M3–M6. No nodes, no VPS deploy, no live ansible."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = ROOT / "src" / "nym-ansible"
CONTROLLER = ROOT / "src" / "nym-controller"
MCP = ROOT / "src" / "nym-mcp"

sys.path.insert(0, str(CONTROLLER))
sys.path.insert(0, str(MCP))

import change_policy as policy  # noqa: E402
import controller_service as svc  # noqa: E402
import controller_runtime as runtime  # noqa: E402
import human_approval as ha  # noqa: E402
import preparation  # noqa: E402


def expiry(hours: int = 2) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


class RunnerValidationTests(unittest.TestCase):
    def setUp(self):
        self.script = ANSIBLE / "run-nym-ansible.sh"
        self.env = os.environ.copy()
        self.env.update(
            {
                "NYM_STAGING_OFFLINE": "1",
                "NYM_ANSIBLE_BASE": str(ANSIBLE),
                "NYM_CONTROLLER_STATE": "",  # filled per test
            }
        )

    def _run(self, args, env=None, state=None):
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(state or d)
            state_dir.mkdir(mode=0o700, exist_ok=True)
            e = dict(self.env if env is None else env)
            e["NYM_CONTROLLER_STATE"] = str(state_dir)
            e["NYM_ANSIBLE_BASE"] = str(ANSIBLE)
            e["NYM_STAGING_OFFLINE"] = "1"
            return subprocess.run(
                ["/usr/bin/bash", str(self.script), *args],
                capture_output=True,
                text=True,
                env=e,
            )

    def test_journal_status_dry_run_ok(self):
        r = self._run(["--dry-run", "--journal-status", "node-b"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("journal_status.yml", r.stdout)

    def test_journal_rejects_bad_hosts(self):
        for limit in ("node-b,node-b", "all", "node-b,", "node-a\ngermany"):
            r = self._run(["--journal-status", limit])
            self.assertEqual(r.returncode, 2, limit)

    def test_restart_requires_mutation_flag(self):
        r = self._run(["--dry-run", "--restart", "node-a"])
        self.assertEqual(r.returncode, 77)

    def test_restart_dry_run_with_mutation(self):
        env = self.env.copy()
        env["NYM_ALLOW_MUTATION"] = "1"
        r = self._run(["--dry-run", "--restart", "node-a"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restart_nym_node.yml", r.stdout)

    def test_restart_rejects_multi_host(self):
        env = self.env.copy()
        env["NYM_ALLOW_MUTATION"] = "1"
        r = self._run(["--dry-run", "--restart", "node-a,node-b"], env=env)
        self.assertEqual(r.returncode, 2)

    def test_update_requires_sealed_metadata(self):
        env = self.env.copy()
        env["NYM_ALLOW_MUTATION"] = "1"
        r = self._run(["--dry-run", "--update-binary", "node-d"], env=env)
        self.assertEqual(r.returncode, 2)
        env.update(
            {
                "NYM_UPDATE_VERSION": "1.43.0",
                "NYM_UPDATE_TAG": "nym-binaries-v1.43.0",
                "NYM_UPDATE_URL": "https://github.com/nymtech/nym/releases/download/nym-binaries-v1.43.0/nym-node",
                "NYM_UPDATE_SHA256": "a" * 64,
            }
        )
        r = self._run(["--dry-run", "--update-binary", "node-d"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_state_directory_fallback_without_nym_controller_state(self):
        """ProtectHome fix: STATE_DIRECTORY used when NYM_CONTROLLER_STATE unset."""
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(d) / "var-lib-nym-controller"
            state_dir.mkdir(mode=0o700)
            e = os.environ.copy()
            e["NYM_STAGING_OFFLINE"] = "1"
            e["NYM_ANSIBLE_BASE"] = str(ANSIBLE)
            e["STATE_DIRECTORY"] = str(state_dir)
            e.pop("NYM_CONTROLLER_STATE", None)
            r = subprocess.run(
                ["/usr/bin/bash", str(self.script), "--dry-run", "--journal-status", "node-a"],
                capture_output=True,
                text=True,
                env=e,
            )
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertTrue((state_dir / "operation.lock").exists())
            mode = oct((state_dir / "operation.lock").stat().st_mode)[-3:]
            self.assertEqual(mode, "600")

    def test_ephemeral_agent_dirs_use_runtime_directory(self):
        """agent.sock parent must be under RuntimeDirectory (host-visible to dockerd)."""
        with tempfile.TemporaryDirectory() as d:
            runtime = Path(d) / "run-nym-controller"
            state = Path(d) / "var-lib-nym-controller"
            runtime.mkdir(mode=0o700)
            state.mkdir(mode=0o700)
            script = self.script.read_text(encoding="utf-8")
            start = script.index("# Ephemeral agent/identity dirs")
            end = script.index("DOCKER_PID=''", start)
            block = script[start:end]
            bash = f"""
set -euo pipefail
NYM_STAGING_OFFLINE=1
RUNTIME_DIRECTORY='{runtime}'
STATE='{state}'
{block}
export AGENT_SOCK RUNTIME_DIRECTORY
printf '%s\\n' "$EPHEMERAL_ROOT" "$AGENT_DIR" "$IDENTITY_DIR" "$AGENT_SOCK"
python3 - <<'P'
import os, socket
sock_path = os.environ["AGENT_SOCK"]
root = os.environ["RUNTIME_DIRECTORY"]
assert os.path.isabs(sock_path)
assert sock_path.startswith(root.rstrip("/") + "/")
assert "/tmp/tmp." not in sock_path
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.bind(sock_path)
finally:
    s.close()
assert os.path.exists(sock_path)
mode = oct(os.stat(os.path.dirname(sock_path)).st_mode)[-3:]
assert mode == "700", mode
print("DOCKER_BIND_PATH_OK")
P
rm -f -- "$AGENT_SOCK"
rmdir -- "$AGENT_DIR" "$IDENTITY_DIR"
"""
            r = subprocess.run(["/usr/bin/bash", "-c", bash], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            lines = r.stdout.strip().splitlines()
            self.assertEqual(lines[0], str(runtime))
            self.assertTrue(lines[1].startswith(str(runtime) + os.sep))
            self.assertTrue(lines[2].startswith(str(runtime) + os.sep))
            self.assertTrue(lines[3].endswith("/agent.sock"))
            self.assertIn("DOCKER_BIND_PATH_OK", lines[-1])

    def test_ephemeral_falls_back_to_state_directory(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state"
            state.mkdir(mode=0o700)
            script = self.script.read_text(encoding="utf-8")
            start = script.index("# Ephemeral agent/identity dirs")
            end = script.index("DOCKER_PID=''", start)
            block = script[start:end]
            bash = f"""
set -euo pipefail
NYM_STAGING_OFFLINE=1
unset RUNTIME_DIRECTORY || true
STATE='{state}'
{block}
printf '%s\\n' "$EPHEMERAL_ROOT" "$AGENT_DIR"
rmdir -- "$AGENT_DIR" "$IDENTITY_DIR"
"""
            r = subprocess.run(["/usr/bin/bash", "-c", bash], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            ephem, agent = r.stdout.strip().splitlines()[:2]
            self.assertEqual(ephem, str(state))
            self.assertTrue(agent.startswith(str(state) + os.sep))

    def test_agent_container_uid_matches_ssh_agent_owner(self):
        """F-M7-AGENTSOCK-UID: container 987:987 matches agent owner; sock 600 only."""
        script = self.script.read_text(encoding="utf-8")
        agent_start = script.index('ssh-agent -a "$AGENT_SOCK"')
        chmod_pos = script.index('chmod 600 "$AGENT_SOCK"', agent_start)
        docker_pos = script.index("docker run", chmod_pos)
        user_pos = script.index("--user 987:987", docker_pos)
        self.assertLess(agent_start, chmod_pos)
        self.assertLess(docker_pos, user_pos)
        self.assertNotIn('chmod 666 "$AGENT_SOCK"', script)
        self.assertNotIn("setfacl", script)
        self.assertNotIn("--user 1001:1001", script)
        self.assertIn("nym-controller:x:987:987", script)
        self.assertIn('chmod 700 "$AGENT_DIR" "$IDENTITY_DIR"', script)
        self.assertNotIn("PrivateTmp=no", script)
        self.assertNotRegex(script, r"--mount[^\n]*docker\.sock")

        with tempfile.TemporaryDirectory() as d:
            agent_dir = Path(d) / "agent.dir"
            agent_dir.mkdir(mode=0o700)
            sock_path = agent_dir / "agent.sock"
            bash = f"""
set -euo pipefail
python3 -c "import socket; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.bind('{sock_path}'); s.close()"
chmod 600 -- '{sock_path}'
mode=$(stat -c '%a' -- '{sock_path}')
[[ "${{mode: -1}}" == 0 ]]
dir_mode=$(stat -c '%a' -- '{agent_dir}')
[[ "$dir_mode" == 700 ]]
chmod 666 -- '{sock_path}'
wmode=$(stat -c '%a' -- '{sock_path}')
[[ "${{wmode: -1}}" != 0 ]]
printf 'AGENT_SOCK_OWNER_UID_FIX_OK\\n'
rm -f -- '{sock_path}'
"""
            r = subprocess.run(["/usr/bin/bash", "-c", bash], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("AGENT_SOCK_OWNER_UID_FIX_OK", r.stdout)

    def test_agent_sock_rejects_world_writable_gate(self):
        """Runner must exit 73 if agent.sock other bits are non-zero."""
        script = self.script.read_text(encoding="utf-8")
        self.assertIn('other_digit="${sock_mode: -1}"', script)
        self.assertIn("agent.sock mode inseguro", script)

    def test_protecthome_path_resolution_probe(self):
        """Resolve BASE/KEY/STATE like nym-controller without touching nodes."""
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state"
            cred = Path(d) / "cred"
            state.mkdir(mode=0o700)
            cred.mkdir(mode=0o700)
            (cred / "ssh_key").write_text("x", encoding="utf-8")
            (cred / "known_hosts").write_text("y", encoding="utf-8")
            header = "\n".join(self.script.read_text(encoding="utf-8").splitlines()[6:30])
            wrapper = header + '\nprintf "%s\\n" "$BASE" "$KEY" "$KNOWN_HOSTS" "$STATE"\n'
            e = {
                "STATE_DIRECTORY": str(state),
                "CREDENTIALS_DIRECTORY": str(cred),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            }
            r = subprocess.run(["/usr/bin/bash", "-c", wrapper], capture_output=True, text=True, env=e)
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = r.stdout.strip().splitlines()
            self.assertEqual(
                lines,
                [
                    "/usr/local/libexec/nym-controller",
                    str(cred / "ssh_key"),
                    str(cred / "known_hosts"),
                    str(state),
                ],
            )


class ControllerRequestTests(unittest.TestCase):
    def test_journal_status_request_ok(self):
        op, hosts = svc.validate_request(
            {"v": 1, "op": "journal_status", "hosts": ["node-e"]}
        )
        self.assertEqual(op, "journal_status")
        self.assertEqual(hosts, ["node-e"])

    def test_mutation_requires_single_host(self):
        with self.assertRaises(svc.RequestError):
            svc.validate_request(
                {"v": 1, "op": "restart_nym_node", "hosts": ["node-a", "node-b"]}
            )

    def test_unknown_op_rejected(self):
        with self.assertRaises(svc.RequestError):
            svc.validate_request({"v": 1, "op": "shell", "hosts": ["node-a"]})


class RuntimeExecuteOfflineTests(unittest.TestCase):
    def _env(self, state: Path, *, mutation: bool = False) -> dict:
        env = {
            "HOME": str(state),
            "USER": "nym-controller",
            "LOGNAME": "nym-controller",
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "CREDENTIALS_DIRECTORY": str(state / "creds"),
            "STATE_DIRECTORY": str(state),
            "RUNTIME_DIRECTORY": str(state / "run"),
            "NYM_STAGING_OFFLINE": "1",
            "NYM_ANSIBLE_BASE": str(ANSIBLE),
            "NYM_CONTROLLER_STATE": str(state),
        }
        (state / "creds").mkdir(exist_ok=True)
        (state / "run").mkdir(exist_ok=True)
        if mutation:
            env["NYM_ALLOW_MUTATION"] = "1"
        return env

    def test_execute_journal_offline_dry_path(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            state.chmod(0o700)
            # Runner with --dry-run is not used by execute; staging offline exits 79
            # without dry-run. Patch: call runner dry-run via execute would need flag.
            # Instead verify argv construction by expecting exit 79 (refuses live).
            code, out, truncated = runtime.execute(
                "journal_status",
                ["node-b"],
                env=self._env(state),
                timeout=30,
                limit=4096,
            )
            self.assertEqual(code, 79)
            self.assertFalse(truncated)

    def test_execute_restart_without_mutation_env_fails_validation(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            state.chmod(0o700)
            with self.assertRaises(runtime.RuntimeErrorSafe):
                runtime.execute(
                    "restart_nym_node",
                    ["node-a"],
                    env=self._env(state, mutation=False),
                    timeout=30,
                    limit=4096,
                )


class ApprovalAndApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        state = Path(self._tmpdir.name)
        state.chmod(0o700)
        self.state = state
        os.environ["STATE_DIRECTORY"] = str(state)
        os.environ[ha.APPROVAL_SECRET_ENV] = "n" * 32
        os.environ.pop(policy.MAINTENANCE_ENV, None)

    def tearDown(self):
        os.environ.pop("STATE_DIRECTORY", None)
        os.environ.pop(ha.APPROVAL_SECRET_ENV, None)
        os.environ.pop(policy.MAINTENANCE_ENV, None)
        self._tmpdir.cleanup()

    def _plan(self, action="restart", host="node-b"):
        req = {
            "targets": [host],
            "action": action,
            "concurrency": 1,
            "expires_at": expiry(),
        }
        if action == "update_binary":
            tag = "nym-binaries-v1.43.0"
            req.update(
                version="1.43.0",
                release_tag=tag,
                source_url=f"https://github.com/nymtech/nym/releases/download/{tag}/nym-node",
                sha256="b" * 64,
            )
        return preparation.prepare(req)

    def _audit(self):
        audits = []

        def audit_writer(operation, hosts, code, extra=None):
            audits.append((operation, hosts, code, extra))

        return audits, audit_writer

    def test_apply_blocked_by_default(self):
        plan = self._plan()
        with self.assertRaises(policy.PolicyError) as ctx:
            policy.apply(plan, approval={"kind": "x"}, executor=lambda *_: {"ok": True})
        self.assertEqual(str(ctx.exception), "maintenance_disabled")

    def test_apply_requires_matching_allows_apply_approval(self):
        plan = self._plan("restart", "node-d")
        os.environ[policy.MAINTENANCE_ENV] = "1"
        bad = ha.build_approval(plan=plan, approver="op", allows_apply=False)
        audits, audit_writer = self._audit()
        with self.assertRaises(policy.PolicyError):
            policy.apply(
                plan,
                approval=bad,
                executor=lambda *_: {"ok": True},
                audit_writer=audit_writer,
            )
        good = ha.build_approval(plan=plan, approver="op", allows_apply=True)
        self.assertIn("mac", good)
        calls = []

        def executor(op, hosts):
            calls.append((op, hosts))
            return {"ok": True, "exit_code": 0, "output": "STAGING"}

        out = policy.apply(
            plan,
            approval=good,
            executor=executor,
            audit_writer=audit_writer,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(calls, [("restart_nym_node", ["node-d"])])
        self.assertEqual(audits[0][0], "approval_recorded")
        # persistent reuse blocked
        with self.assertRaises(policy.PolicyError):
            policy.apply(
                plan,
                approval=good,
                executor=executor,
                audit_writer=audit_writer,
            )

    def test_stale_approval_other_plan_rejected(self):
        plan_a = self._plan("restart", "node-a")
        plan_b = self._plan("restart", "node-e")
        appr = ha.build_approval(plan=plan_a, approver="op", allows_apply=True)
        os.environ[policy.MAINTENANCE_ENV] = "1"
        _, audit_writer = self._audit()
        with self.assertRaises(policy.PolicyError):
            policy.apply(
                plan_b,
                approval=appr,
                executor=lambda *_: {"ok": True},
                audit_writer=audit_writer,
            )

    def test_update_plan_rejects_tag_html_url(self):
        with self.assertRaises(policy.PolicyError):
            preparation.prepare(
                {
                    "targets": ["node-f"],
                    "action": "update_binary",
                    "concurrency": 1,
                    "expires_at": expiry(),
                    "version": "1.43.0",
                    "release_tag": "nym-binaries-v1.43.0",
                    "source_url": "https://github.com/nymtech/nym/releases/tag/nym-binaries-v1.43.0",
                    "sha256": "c" * 64,
                }
            )

    def test_update_plan_requires_sha256(self):
        with self.assertRaises(policy.PolicyError):
            preparation.prepare(
                {
                    "targets": ["node-f"],
                    "action": "update_binary",
                    "concurrency": 1,
                    "expires_at": expiry(),
                    "version": "1.43.0",
                    "release_tag": "nym-binaries-v1.43.0",
                    "source_url": "https://github.com/nymtech/nym/releases/download/nym-binaries-v1.43.0/nym-node",
                }
            )

    def test_apply_requires_audit_writer(self):
        plan = self._plan()
        os.environ[policy.MAINTENANCE_ENV] = "1"
        good = ha.build_approval(plan=plan, approver="op", allows_apply=True)
        with self.assertRaises(policy.PolicyError) as ctx:
            policy.apply(plan, approval=good, executor=lambda *_: {"ok": True})
        self.assertEqual(str(ctx.exception), "audit_writer_required")

    def test_approval_secret_from_credentials_directory_file(self):
        """LoadCredential-style file: $CREDENTIALS_DIRECTORY/approval_secret."""
        os.environ.pop(ha.APPROVAL_SECRET_ENV, None)
        with tempfile.TemporaryDirectory() as d:
            cred = Path(d)
            # synthetic offline fixture only — not a production secret
            fixture = ("N" * 32).encode("ascii")
            (cred / ha.APPROVAL_SECRET_CREDENTIAL).write_bytes(fixture + b"\n")
            os.environ["CREDENTIALS_DIRECTORY"] = str(cred)
            try:
                got = ha.approval_secret()
                self.assertEqual(got, fixture)
                plan = self._plan("restart", "node-a")
                appr = ha.build_approval(plan=plan, approver="op", allows_apply=True)
                self.assertTrue(appr["allows_apply"])
                self.assertIn("mac", appr)
                self.assertEqual(appr["targets"], ["node-a"])
                self.assertEqual(appr["action"], "restart")
                self.assertTrue(appr["single_use"])
                self.assertRegex(appr["plan_id"], r"^[0-9a-f]{64}$")
                verified = ha.verify_approval_for_plan(
                    appr, plan, expect_allows_apply=True, consumed_ids=set()
                )
                self.assertTrue(verified["ok"])
            finally:
                os.environ.pop("CREDENTIALS_DIRECTORY", None)
                os.environ.pop(ha.APPROVAL_SECRET_ENV, None)

    def test_approval_secret_missing_credential_returns_none(self):
        os.environ.pop(ha.APPROVAL_SECRET_ENV, None)
        with tempfile.TemporaryDirectory() as d:
            os.environ["CREDENTIALS_DIRECTORY"] = d
            try:
                self.assertIsNone(ha.approval_secret())
            finally:
                os.environ.pop("CREDENTIALS_DIRECTORY", None)

    def test_approval_binds_node_op_plan_expiry_single_use(self):
        plan = self._plan("restart", "node-a")
        appr = ha.build_approval(plan=plan, approver="op", allows_apply=True)
        self.assertEqual(appr["targets"], ["node-a"])
        self.assertEqual(appr["action"], "restart")
        self.assertEqual(appr["plan_id"], plan["plan_id"])
        self.assertTrue(appr["single_use"])
        self.assertRegex(appr["plan_content_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(appr["expires_at"], r"Z$")
        other = self._plan("restart", "node-b")
        with self.assertRaises(ha.ApprovalError):
            ha.verify_approval_for_plan(appr, other, expect_allows_apply=True)
        consumed: set[str] = set()
        ha.consume_approval(appr, plan, consumed_ids=consumed, expect_allows_apply=True)
        with self.assertRaises(ha.ApprovalError):
            ha.consume_approval(appr, plan, consumed_ids=consumed, expect_allows_apply=True)


class PlaybookPresenceTests(unittest.TestCase):
    def test_required_playbooks_exist(self):
        for name in (
            "journal_status.yml",
            "restart_nym_node.yml",
            "update_nym_node.yml",
            "monitor.yml",
            "ping.yml",
        ):
            self.assertTrue((ANSIBLE / name).is_file(), name)

    def test_journal_playbook_forbids_message_field(self):
        text = (ANSIBLE / "journal_status.yml").read_text()
        self.assertIn("--output-fields=PRIORITY", text)
        self.assertNotIn("--output-fields=MESSAGE", text)
        self.assertNotIn("output-fields=PRIORITY,MESSAGE", text)
        self.assertIn("messages_emitted=False", text)


class AuditExtraTests(unittest.TestCase):
    def test_audit_approval_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            state.chmod(0o700)
            os.environ["STATE_DIRECTORY"] = str(state)
            try:
                path = state / "audit.jsonl"
                runtime.audit(
                    path,
                    "approval_recorded",
                    ["node-b"],
                    0,
                    1,
                    False,
                    extra={
                        "approval_id": "a" * 64,
                        "plan_id": "b" * 64,
                        "action": "restart",
                    },
                )
                rec = json.loads(path.read_text().splitlines()[-1])
                self.assertEqual(rec["operation"], "approval_recorded")
                self.assertEqual(rec["action"], "restart")
                self.assertNotIn("plan", rec)
            finally:
                os.environ.pop("STATE_DIRECTORY", None)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "was_successful": result.wasSuccessful(),
        "failure_details": [
            {"test": str(t), "trace": tb} for t, tb in result.failures + result.errors
        ],
    }
    out = Path(__file__).resolve().parent / ".m3-m6-offline-test-evidence.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(out), **{k: report[k] for k in ("tests_run", "failures", "errors", "was_successful")}}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
