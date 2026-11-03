"""Offline evidence analysis and content-addressed plans. No executor or I/O."""
from datetime import datetime, timezone
import hmac
import math
import re
import change_policy as policy

PolicyError = policy.PolicyError
parse_json = policy.parse_json
HOSTS = policy.TARGETS
ACTIONS = policy.ACTIONS


def _hosts(hosts):
    if (type(hosts) is not list or not hosts or
            any(type(h) is not str or h not in HOSTS for h in hosts) or
            len(set(hosts)) != len(hosts)):
        raise PolicyError('invalid_targets')
    return sorted(hosts)


def _clock(now):
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise PolicyError('invalid_clock')
    return now


def _stamp(value):
    if type(value) is not str or re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', value) is None:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def preflight(hosts, evidence, *, now=None, max_age_seconds=900):
    """Analyze caller-supplied monitor records; never collect or authenticate them.

    Evidence is a list of structured records from controller safe_output (not
    raw Ansible text). Unknown fields are rejected; missing/invalid measurements
    are explicit findings. OS/backup are unavailable in the existing monitor.
    """
    selected = _hosts(hosts)
    now = _clock(now)
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400:
        raise PolicyError('invalid_max_age')
    if type(evidence) is not list:
        raise PolicyError('invalid_evidence')
    fields = {'host', 'checked_at', 'version', 'state', 'service_state',
              'disk_used_pct', 'memory_used_pct', 'load_per_cpu',
              'recent_error_count', 'journal_sample_capped', 'restart_count'}
    records = {}
    for record in evidence:
        if type(record) is not dict or set(record) - fields:
            raise PolicyError('invalid_evidence_fields')
        host = record.get('host')
        if type(host) is not str or host not in selected or host in records:
            raise PolicyError('invalid_evidence_host')
        records[host] = record
    results = []
    for host in selected:
        record = records.get(host, {})
        findings = []
        if not record:
            findings.append('missing_evidence')
        stamp = _stamp(record.get('checked_at'))
        age = (now - stamp).total_seconds() if stamp is not None else None
        freshness = ('unknown' if age is None else 'future' if age < 0 else
                     'stale' if age > max_age_seconds else 'fresh')
        if freshness != 'fresh':
            findings.append('evidence_' + freshness)
        value = record.get('version')
        match = re.fullmatch(r'nym-node\s+v?((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:[-+][A-Za-z0-9.-]{1,80})?)', value) if type(value) is str else None
        version = match.group(1) if match else 'unknown'
        if version == 'unknown':
            findings.append('version_unknown')
        service = record.get('service_state')
        if type(service) is not str or service not in {'active', 'inactive', 'failed', 'activating', 'deactivating'}:
            service = 'unknown'
        if service != 'active':
            findings.append('service_' + service)
        disk = record.get('disk_used_pct')
        if type(disk) is int:
            disk_valid = 0 <= disk <= 100
        elif type(disk) is float:
            disk_valid = math.isfinite(disk) and 0 <= disk <= 100
        else:
            disk_valid = False
        if not disk_valid:
            disk = None
        disk_state = 'unknown' if disk is None else 'critical' if disk >= 90 else 'warning' if disk >= 80 else 'ok'
        if disk_state != 'ok':
            findings.append('disk_' + disk_state)
        findings.extend(['os_unknown', 'backup_unknown', 'compatibility_unknown'])
        results.append(dict(host=host, freshness=freshness, age_seconds=age,
                            version=version, service_state=service, disk_used_pct=disk,
                            disk_state=disk_state, os='unknown', backup='unknown',
                            compatibility='unknown', findings=findings,
                            maintenance_ready=False))
    return dict(mode='read_only', evidence_origin='caller_supplied_unverified',
                maintenance_enabled=False, hosts=results)


def _release(request):
    tag, url, digest = request.get('release_tag'), request.get('source_url'), request.get('sha256')
    if type(tag) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', tag) is None:
        raise PolicyError('invalid_release_tag')
    # Artifact download URL only (not the HTML /releases/tag/ page).
    if type(url) is not str or re.fullmatch(
            r'https://github\.com/nymtech/nym/releases/download/'
            + re.escape(tag)
            + r'/[A-Za-z0-9][A-Za-z0-9._+-]{0,127}',
            url) is None:
        raise PolicyError('invalid_official_source_url')
    if type(digest) is not str or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
        raise PolicyError('invalid_sha256')
    return dict(release_tag=tag, source_url=url, sha256=digest, official_url=True,
                verified=False, version_tag_binding='unverified',
                artifact_integrity='sha256_declared_unverified', compatibility='unknown')


def prepare(request, *, now=None):
    """Generate only a plan; release tags are metadata, never commands/versions."""
    if type(request) is not dict:
        raise PolicyError('invalid_request')
    core = dict(request)
    release = None
    if request.get('action') == 'update_binary':
        release = _release(request)
        core.pop('release_tag', None)
        core.pop('source_url', None)
        core.pop('sha256', None)
    contract = policy.prepare(core, now=now)
    content = dict(objective='plan_only', maintenance_enabled=False,
                   contract=contract, release=release)
    return dict(content, plan_id=policy._identity(content))


def validate(plan, *, now=None):
    """Integrity and expiry only; hashes do NOT authenticate an approval."""
    if type(plan) is not dict or set(plan) != {'objective', 'maintenance_enabled', 'contract', 'release', 'plan_id'}:
        raise PolicyError('invalid_plan')
    contract = policy.validate(plan['contract'], now=now)
    request = {k: v for k, v in contract.items() if k != 'plan_id'}
    if contract['action'] == 'update_binary':
        release = plan['release']
        if type(release) is not dict:
            raise PolicyError('invalid_release')
        request.update(
            release_tag=release.get('release_tag'),
            source_url=release.get('source_url'),
            sha256=release.get('sha256'),
        )
    if type(plan['plan_id']) is not str or re.fullmatch(r'[0-9a-f]{64}', plan['plan_id']) is None:
        raise PolicyError('invalid_plan_id')
    expected = prepare(request, now=now)
    if not hmac.compare_digest(plan['plan_id'], expected['plan_id']):
        raise PolicyError('plan_hash_mismatch')
    # Exact JSON equality alone treats 0 == False: enforce booleans explicitly.
    if plan['maintenance_enabled'] is not False or plan != expected:
        raise PolicyError('noncanonical_plan')
    if plan['release'] is not None and (plan['release'].get('verified') is not False or plan['release'].get('official_url') is not True):
        raise PolicyError('noncanonical_plan')
    return expected


def apply(*args, **kwargs):
    """Always blocked, including missing, expired or claimed-approved inputs."""
    return policy.apply(*args, **kwargs)
