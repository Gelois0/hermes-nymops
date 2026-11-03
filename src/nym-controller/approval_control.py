"""Controller-side signed approval verifier reused from the offline Nym controls candidate.

No trusted operator keys are enrolled here. This module verifies plans only; it
never invokes Ansible or enables maintenance.
"""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile
from types import MappingProxyType

OPENSSL = "/usr/bin/openssl"
MAX_FRAME = 32768
MAX_RPC_FRAME = MAX_FRAME + 4096
from allowed_hosts import allowed_host_set
HOSTS = allowed_host_set()
OPERATIONS = frozenset(("restart", "update_binary", "restore"))
# Deployment-owned public keys only. Deliberately empty until an explicit enrollment approval.
TRUSTED_OPERATOR_KEYS = MappingProxyType({})
FIELDS = tuple("schema operation hosts current_versions target_versions artifact_digest implementation_digest inventory_digest issued_at expires_at operator nonce batches concurrency backup_policy rollback_policy health_policy operation_evidence".split())


class ApprovalRejected(ValueError):
    """Fail-closed plan or signature rejection; contains no user-controlled text."""


def _matches(value, pattern):
    return type(value) is str and re.fullmatch(pattern, value) is not None


def _exact(obj, keys):
    if type(obj) is not dict or set(obj) != set(keys):
        raise ApprovalRejected("schema")


class Plan:
    __slots__ = ("_payload",)

    def __setattr__(self, key, value):
        raise AttributeError("immutable plan")

    @classmethod
    def from_mapping(cls, obj):
        _exact(obj, FIELDS)
        if obj["schema"] != "nym-maintenance-plan-v2" or obj["operation"] not in OPERATIONS:
            raise ApprovalRejected("schema_or_operation")
        hosts = obj["hosts"]
        if (type(hosts) is not list or not hosts or
                any(type(host) is not str or host not in HOSTS for host in hosts) or
                len(set(hosts)) != len(hosts)):
            raise ApprovalRejected("hosts")
        for field in ("current_versions", "target_versions"):
            _exact(obj[field], hosts)
            if any(not _matches(version, r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}")
                   for version in obj[field].values()):
                raise ApprovalRejected("versions")
        for field in ("artifact_digest", "implementation_digest", "inventory_digest"):
            if not _matches(obj[field], r"[a-f0-9]{64}"):
                raise ApprovalRejected("digest")
        if (not _matches(obj["operator"], r"[A-Za-z0-9_-]{1,64}") or
                not _matches(obj["nonce"], r"[A-Za-z0-9_-]{16,128}")):
            raise ApprovalRejected("identity")
        if (any(type(obj[key]) is not int for key in ("issued_at", "expires_at", "concurrency")) or
                not 0 <= obj["issued_at"] < obj["expires_at"] < 2**53 or
                not 1 <= obj["expires_at"] - obj["issued_at"] <= 900 or
                not 1 <= obj["concurrency"] <= len(hosts)):
            raise ApprovalRejected("time_or_concurrency")
        batches = obj["batches"]
        if (type(batches) is not list or not batches or
                any(type(batch) is not list or not 1 <= len(batch) <= obj["concurrency"]
                    for batch in batches)):
            raise ApprovalRejected("batches")
        flat = [host for batch in batches for host in batch]
        if (any(type(host) is not str for host in flat) or len(flat) != len(hosts) or
                set(flat) != set(hosts)):
            raise ApprovalRejected("batch_coverage")
        _exact(obj["backup_policy"], ("required", "max_age_seconds"))
        if (obj["backup_policy"]["required"] is not True or
                type(obj["backup_policy"]["max_age_seconds"]) is not int or
                not 1 <= obj["backup_policy"]["max_age_seconds"] <= 900):
            raise ApprovalRejected("backup_policy")
        _exact(obj["rollback_policy"], ("mode", "on_failure"))
        if obj["rollback_policy"] != {"mode": "restore_previous", "on_failure": "halt"}:
            raise ApprovalRejected("rollback_policy")
        _exact(obj["health_policy"], ("consecutive_passes", "timeout_seconds"))
        for key, maximum in (("consecutive_passes", 10), ("timeout_seconds", 300)):
            if type(obj["health_policy"][key]) is not int or not 1 <= obj["health_policy"][key] <= maximum:
                raise ApprovalRejected("health_policy")
        evidence = obj["operation_evidence"]
        if obj["operation"] == "restart":
            _exact(evidence, ("service", "preflight", "postflight"))
            if evidence != {"service": "nym-node.service", "preflight": "must_be_active", "postflight": "must_be_active"}:
                raise ApprovalRejected("restart_evidence")
        elif obj["operation"] == "update_binary":
            _exact(evidence, ("release_tag", "source_url", "sha256", "binary_path", "compatibility_report_path", "compatibility_digest", "rollback_backup_digest"))
            tag = evidence["release_tag"]
            if (not _matches(tag, r"[A-Za-z0-9._-]{1,128}") or
                    evidence["source_url"] != f"https://github.com/nymtech/nym/releases/download/{tag}/nym-node" or
                    evidence["sha256"] != obj["artifact_digest"] or
                    not _matches(evidence["binary_path"], r"/[A-Za-z0-9._/-]{1,255}") or ".." in Path(evidence["binary_path"]).parts or
                    not _matches(evidence["compatibility_report_path"], r"/work/[A-Za-z0-9._/-]{1,240}") or ".." in Path(evidence["compatibility_report_path"]).parts or
                    not _matches(evidence["compatibility_digest"], r"[a-f0-9]{64}") or
                    not _matches(evidence["rollback_backup_digest"], r"[a-f0-9]{64}")):
                raise ApprovalRejected("update_evidence")
        elif obj["operation"] == "restore":
            _exact(evidence, ("backup_id", "backup_path", "backup_sha256", "backup_bytes", "backup_verification_report_path", "backup_verification_digest", "restore_policy_path", "restore_policy_digest", "binary_path"))
            if (not _matches(evidence["backup_id"], r"[A-Za-z0-9_-]{1,64}") or
                    not _matches(evidence["backup_path"], r"/[A-Za-z0-9._/-]{1,255}") or ".." in Path(evidence["backup_path"]).parts or
                    not _matches(evidence["backup_sha256"], r"[a-f0-9]{64}") or
                    evidence["backup_sha256"] != obj["artifact_digest"] or
                    type(evidence["backup_bytes"]) is not int or not 1 <= evidence["backup_bytes"] <= 2**40 or
                    not _matches(evidence["backup_verification_report_path"], r"/work/[A-Za-z0-9._/-]{1,240}") or ".." in Path(evidence["backup_verification_report_path"]).parts or
                    not _matches(evidence["backup_verification_digest"], r"[a-f0-9]{64}") or
                    not _matches(evidence["restore_policy_path"], r"/work/[A-Za-z0-9._/-]{1,240}") or ".." in Path(evidence["restore_policy_path"]).parts or
                    not _matches(evidence["restore_policy_digest"], r"[a-f0-9]{64}") or
                    not _matches(evidence["binary_path"], r"/[A-Za-z0-9._/-]{1,255}") or ".." in Path(evidence["binary_path"]).parts):
                raise ApprovalRejected("restore_evidence")
        canonical = json.dumps(dict(obj, hosts=sorted(hosts)), sort_keys=True,
                               separators=(",", ":"), ensure_ascii=True).encode("ascii")
        if len(canonical) > MAX_FRAME:
            raise ApprovalRejected("oversize")
        plan = object.__new__(cls)
        object.__setattr__(plan, "_payload", canonical)
        return plan

    def canonical(self):
        return self._payload

    def mapping(self):
        return json.loads(self._payload)

    def digest(self):
        return hashlib.sha256(self._payload).hexdigest()


def prepare_admin_plan(mapping):
    plan = Plan.from_mapping(mapping)
    return {
        "canonical_plan": plan.canonical().decode("ascii"),
        "plan_digest": plan.digest(),
        "maintenance_enabled": False,
        "execution": "not_executed",
    }


def verify_ed25519(public_key, message, signature):
    if type(public_key) is not bytes or len(public_key) != 32:
        raise ApprovalRejected("public_key")
    if type(message) is not bytes or not 1 <= len(message) <= MAX_FRAME:
        raise ApprovalRejected("message")
    try:
        if type(signature) is not str or len(signature) != 88:
            raise ValueError()
        raw = base64.b64decode(signature, validate=True)
        if len(raw) != 64 or base64.b64encode(raw).decode("ascii") != signature:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise ApprovalRejected("signature_encoding") from exc
    with tempfile.TemporaryDirectory(prefix="nym-public-verify-") as directory:
        root = Path(directory)
        for name, data in (("public.der", bytes.fromhex("302a300506032b6570032100") + public_key),
                           ("message", message), ("signature", raw)):
            fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
        try:
            result = subprocess.run(
                [OPENSSL, "pkeyutl", "-verify", "-pubin", "-keyform", "DER",
                 "-inkey", str(root / "public.der"), "-rawin", "-in", str(root / "message"),
                 "-sigfile", str(root / "signature")],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2, env={"PATH": "/usr/bin:/bin", "LANG": "C", "OPENSSL_CONF": "/dev/null"},
                cwd=directory, close_fds=True, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ApprovalRejected("crypto_unavailable_or_timeout") from exc
        if result.returncode != 0:
            raise ApprovalRejected("signature")
    return True


def _validate_state_parent(parent):
    parent = Path(parent)
    if not parent.is_absolute():
        raise ApprovalRejected("absolute_state_path_required")
    current = Path("/")
    for component in parent.parts[1:]:
        current = current / component
        try:
            info = current.lstat()
        except OSError as exc:
            raise ApprovalRejected("state_parent_unavailable") from exc
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()):
            raise ApprovalRejected("state_parent_owner")
        mode = stat.S_IMODE(info.st_mode)
        if current == parent:
            if info.st_uid != os.getuid() or mode != 0o700:
                raise ApprovalRejected("private_state_directory_required")
        elif mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ApprovalRejected("unsafe_state_ancestor")
    return parent


class NonceStore:
    """Private SQLite replay ledger in the controller-owned state directory."""
    def __init__(self, path):
        self.path = Path(path)
        try:
            _validate_state_parent(self.path.parent)
            if self.path.is_symlink():
                raise ApprovalRejected("state_symlink")
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
            self._check_file()
            with closing(sqlite3.connect(str(self.path), timeout=2)) as db:
                with db:
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute("CREATE TABLE IF NOT EXISTS claims (operator TEXT NOT NULL, nonce TEXT NOT NULL, expires INTEGER NOT NULL, plan_digest TEXT NOT NULL, PRIMARY KEY(operator, nonce))")
        except (OSError, sqlite3.Error) as exc:
            raise ApprovalRejected("state_failure") from exc

    def _check_file(self):
        if self.path.is_symlink():
            raise ApprovalRejected("state_symlink")
        info = self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ApprovalRejected("unsafe_state_file")
        return info.st_dev, info.st_ino

    def claim(self, operator, nonce, expires, plan_digest, *, now):
        if (not _matches(operator, r"[A-Za-z0-9_-]{1,64}") or
                not _matches(nonce, r"[A-Za-z0-9_-]{16,128}") or
                not _matches(plan_digest, r"[a-f0-9]{64}") or
                type(now) is not int or type(expires) is not int or
                not 0 <= now < expires or expires - now > 900):
            raise ApprovalRejected("claim_bounds")
        identity = self._check_file()
        try:
            with closing(sqlite3.connect(str(self.path), timeout=2)) as db:
                with db:
                    db.execute("PRAGMA synchronous=FULL")
                    if self._check_file() != identity:
                        raise ApprovalRejected("state_replaced")
                    db.execute("DELETE FROM claims WHERE expires <= ?", (now,))
                    db.execute("INSERT INTO claims(operator,nonce,expires,plan_digest) VALUES (?,?,?,?)",
                                (operator, nonce, expires, plan_digest))
        except sqlite3.IntegrityError as exc:
            raise ApprovalRejected("replay") from exc
        except (OSError, sqlite3.Error) as exc:
            raise ApprovalRejected("state_failure") from exc


def verify_admin_approval(mapping, signature, *, state_dir, now, trusted_keys=None, verifier=None):
    plan = Plan.from_mapping(mapping)
    obj = plan.mapping()
    if type(now) is not int or not obj["issued_at"] <= now < obj["expires_at"]:
        raise ApprovalRejected("expired_or_invalid_clock")
    trusted_keys = TRUSTED_OPERATOR_KEYS if trusted_keys is None else trusted_keys
    public_key = trusted_keys.get(obj["operator"]) if hasattr(trusted_keys, "get") else None
    if type(public_key) is not bytes:
        raise ApprovalRejected("untrusted_operator")
    verify = verify_ed25519 if verifier is None else verifier
    verify(public_key, b"NYM-MAINTENANCE-PLAN-v2\x00" + plan.canonical(), signature)
    store = NonceStore(Path(state_dir) / "approval-nonces.sqlite3")
    store.claim(obj["operator"], obj["nonce"], obj["expires_at"], plan.digest(), now=now)
    return {
        "approval_state": "verified_one_time",
        "operator": obj["operator"],
        "operation": obj["operation"],
        "hosts": obj["hosts"],
        "plan_digest": plan.digest(),
        "expires_at": obj["expires_at"],
        "maintenance_enabled": False,
        "execution": "not_executed",
    }
