"""Offline preparation only; no authorization or executable maintenance path.

API: prepare(request, *, now=None), validate(plan, *, now=None), parse_json(text).
UTC timestamps use YYYY-MM-DDTHH:MM:SSZ. Versions are numeric X.Y.Z only.
plan_id is SHA256 of all remaining fields in canonical ASCII JSON (sorted keys,
compact separators, sorted targets). Hash is integrity/identity, NOT a signature:
any client can recompute it. No approval flag is accepted or verified.
apply ALWAYS raises maintenance_disabled, without validating or doing any I/O.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone

from allowed_hosts import allowed_host_set
TARGETS = allowed_host_set()
ACTIONS = frozenset(('start', 'stop', 'restart', 'update_binary'))

class PolicyError(ValueError):
    """Closed-policy rejection; never a request to run a command."""

def parse_json(text):
    """Decode JSON rejecting duplicate keys at any depth and nonfinite numbers."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PolicyError('duplicate_argument')
            result[key] = value
        return result
    def invalid_constant(value):
        raise PolicyError('invalid_json_constant')
    if type(text) is not str:
        raise PolicyError('invalid_json')
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise PolicyError('invalid_json') from exc

def _checked(request, now):
    if type(request) is not dict:
        raise PolicyError('invalid_request')
    action = request.get('action')
    if type(action) is not str or action not in ACTIONS:
        raise PolicyError('invalid_action')
    fields = {'targets', 'action', 'concurrency', 'expires_at'}
    if action == 'update_binary':
        fields.add('version')
    if set(request) != fields:
        raise PolicyError('unexpected_or_missing_argument')
    targets = request['targets']
    if (type(targets) is not list or not targets or
        any(type(t) is not str or t not in TARGETS for t in targets) or
        len(set(targets)) != len(targets)):
        raise PolicyError('invalid_targets')
    concurrency = request['concurrency']
    if type(concurrency) is not int or not 1 <= concurrency <= len(targets):
        raise PolicyError('invalid_concurrency')
    expiry = request['expires_at']
    if type(expiry) is not str or re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', expiry) is None:
        raise PolicyError('invalid_expiry')
    try:
        expiration = datetime.strptime(expiry, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PolicyError('invalid_expiry') from exc
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise PolicyError('invalid_clock')
    if expiration <= now:
        raise PolicyError('expired')
    result = dict(targets=sorted(targets), action=action, concurrency=concurrency, expires_at=expiry)
    if action == 'update_binary':
        version = request['version']
        if type(version) is not str or re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version) is None:
            raise PolicyError('invalid_version')
        result['version'] = version
    return result

def _identity(content):
    canonical = json.dumps(content, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(canonical.encode('ascii')).hexdigest()

def prepare(request, *, now=None):
    """Return normalized content plus identity; does not approve any maintenance."""
    content = _checked(request, now)
    return dict(content, plan_id=_identity(content))

def validate(plan, *, now=None):
    """Check schema, expiry, canonical order and content-bound identity offline."""
    if type(plan) is not dict:
        raise PolicyError('invalid_plan')
    identity = plan.get('plan_id')
    if type(identity) is not str or re.fullmatch(r'[0-9a-f]{64}', identity) is None:
        raise PolicyError('invalid_plan_id')
    content = {k: v for k, v in plan.items() if k != 'plan_id'}
    checked = _checked(content, now)
    if content != checked:
        raise PolicyError('noncanonical_plan')
    if not hmac.compare_digest(identity, _identity(checked)):
        raise PolicyError('plan_hash_mismatch')
    return dict(checked, plan_id=identity)

def apply(*args, **kwargs):
    """Unconditional hard stop, even for valid plans or claimed client approval."""
    raise PolicyError('maintenance_disabled')
