#!/usr/bin/env bash
# Staging-extended fixed runner. Same security model as production:
# allowlisted hosts/playbooks only; no free argv; mutations require NYM_ALLOW_MUTATION=1.
set -Eeuo pipefail

IMAGE='nikolaik/python-nodejs@sha256:8f958bdc1b4a422bfafd97cab4f69836401f616ae985d4b57a53d254f5bcb038'
# ProtectHome=yes: under nym-controller, prefer StateDirectory / LoadCredential / libexec.
# Explicit NYM_* overrides still win. Hermes ops defaults remain when those are unset.
if [[ -n "${NYM_ANSIBLE_BASE:-}" ]]; then
  BASE="$NYM_ANSIBLE_BASE"
elif [[ -n "${STATE_DIRECTORY:-}" ]]; then
  BASE=/usr/local/libexec/nym-controller
else
  BASE=/usr/local/libexec/nym-controller
fi
if [[ -n "${NYM_SSH_KEY:-}" ]]; then
  KEY="$NYM_SSH_KEY"
elif [[ -n "${CREDENTIALS_DIRECTORY:-}" ]]; then
  KEY="$CREDENTIALS_DIRECTORY/ssh_key"
else
  KEY=/etc/nym-controller/ssh_key
fi
if [[ -n "${NYM_KNOWN_HOSTS:-}" ]]; then
  KNOWN_HOSTS="$NYM_KNOWN_HOSTS"
elif [[ -n "${CREDENTIALS_DIRECTORY:-}" ]]; then
  KNOWN_HOSTS="$CREDENTIALS_DIRECTORY/known_hosts"
else
  KNOWN_HOSTS=/etc/nym-controller/known_hosts
fi
STATE="${NYM_CONTROLLER_STATE:-${STATE_DIRECTORY:-/var/lib/nym-controller}}"

# Comma-separated allowlist; override with NYM_ALLOWED_HOSTS for your deployment.
NYM_ALLOWED_HOSTS="${NYM_ALLOWED_HOSTS:-node-a,node-b,node-c,node-d,node-e,node-f}"
IFS=',' read -r -a _ALLOWED_HOSTS <<< "$NYM_ALLOWED_HOSTS"

usage() {
  printf 'Usage: %s [--dry-run] (--monitor|--context-diagnostic|--journal-status|--restart|--update-binary) HOSTS\n' "$0"
  printf 'Without host flags: ping.yml against the full allowlist.\n'
}

host_allowed() {
  local candidate="$1" h
  for h in "${_ALLOWED_HOSTS[@]}"; do
    [[ "$candidate" == "$h" ]] && return 0
  done
  return 1
}

validate_limit() {
  local limit="$1"
  # Reject empty, whitespace, leading/trailing/duplicate commas, and newlines.
  [[ -n "$limit" ]] || return 2
  [[ "$limit" != *$'\n'* && "$limit" != *$'\r'* ]] || return 2
  [[ "$limit" != *,*',' && "$limit" != ','* && "$limit" != *',' ]] || return 2
  [[ "$limit" != *',,'* ]] || return 2
  declare -A SEEN=()
  local -a SELECTED
  IFS=',' read -r -a SELECTED <<< "$limit"
  [[ ${#SELECTED[@]} -ge 1 && ${#SELECTED[@]} -le ${#_ALLOWED_HOSTS[@]} ]] || return 2
  local host
  for host in "${SELECTED[@]}"; do
    [[ -n "$host" ]] || return 2
    [[ "$host" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || return 2
    [[ -z "${SEEN[$host]:-}" ]] || return 2
    SEEN[$host]=1
    host_allowed "$host" || return 2
  done
  return 0
}

if [[ "${1:-}" == '--help' || "${1:-}" == '-h' ]]; then
  usage
  exit 0
fi

DRY_RUN=0
if [[ "${1:-}" == '--dry-run' ]]; then
  DRY_RUN=1
  shift
fi

PLAYBOOK=ping.yml
LIMIT="$NYM_ALLOWED_HOSTS"
MUTATION=0

case "${1:-}" in
  --monitor|--context-diagnostic|--journal-status)
    [[ $# == 2 ]] || exit 2
    case "$1" in
      --monitor) PLAYBOOK=monitor.yml ;;
      --context-diagnostic) PLAYBOOK=context-diagnostic.yml ;;
      --journal-status) PLAYBOOK=journal_status.yml ;;
    esac
    LIMIT="$2"
    validate_limit "$LIMIT" || exit 2
    shift 2
    ;;
  --restart|--update-binary)
    [[ $# == 2 ]] || exit 2
    [[ "${NYM_ALLOW_MUTATION:-0}" == "1" ]] || exit 77
    MUTATION=1
    case "$1" in
      --restart) PLAYBOOK=restart_nym_node.yml ;;
      --update-binary)
        PLAYBOOK=update_nym_node.yml
        [[ "${NYM_UPDATE_VERSION:-}" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || exit 2
        [[ -n "${NYM_UPDATE_TAG:-}" && ${#NYM_UPDATE_TAG} -le 128 ]] || exit 2
        [[ "${NYM_UPDATE_URL:-}" =~ ^https:// ]] || exit 2
        [[ ${#NYM_UPDATE_URL} -le 256 ]] || exit 2
        [[ "${NYM_UPDATE_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] || exit 2
        ;;
    esac
    LIMIT="$2"
    validate_limit "$LIMIT" || exit 2
    # Mutations are single-host only in staging/runtime policy.
    [[ "$LIMIT" != *,* ]] || exit 2
    shift 2
    ;;
  "")
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

export NYM_PLAYBOOK="$PLAYBOOK" NYM_LIMIT="$LIMIT"

if (( $# != 0 )); then
  usage >&2
  exit 2
fi

umask 077
mkdir -p -- "$STATE"
[[ ! -L "$STATE" && -d "$STATE" ]] || exit 73
# Ownership check relaxed only when NYM_STAGING_OFFLINE=1 (workspace tests).
if [[ "${NYM_STAGING_OFFLINE:-0}" != "1" ]]; then
  [[ -O "$STATE" && "$(stat -c %a "$STATE")" == 700 ]] || exit 73
fi
[[ ! -L "$STATE/operation.lock" ]] || exit 73
exec 9>>"$STATE/operation.lock"
if [[ "${NYM_STAGING_OFFLINE:-0}" != "1" ]]; then
  [[ -O "$STATE/operation.lock" && "$(stat -c %a "$STATE/operation.lock")" == 600 ]] || exit 73
fi
flock -n 9 || { printf 'Controller busy.\n' >&2; exit 75; }

for required in "$BASE/inventory.ini" "$BASE/ansible.cfg" "$BASE/$PLAYBOOK"; do
  if [[ ! -f "$required" ]]; then
    printf 'Falta archivo requerido: %s\n' "$required" >&2
    exit 1
  fi
done

if [[ "${NYM_STAGING_OFFLINE:-0}" == "1" ]]; then
  if (( DRY_RUN )); then
    printf 'STAGING offline dry-run ok playbook=%s limit=%s mutation=%s\n' "$PLAYBOOK" "$LIMIT" "$MUTATION"
    exit 0
  fi
  printf 'STAGING offline refuses live ansible/docker execution.\n' >&2
  exit 79
fi

for required in "$KEY" "$KNOWN_HOSTS"; do
  if [[ ! -f "$required" ]]; then
    printf 'Falta archivo requerido: %s\n' "$required" >&2
    exit 1
  fi
done

DOCKER_HOST='tcp://127.0.0.1:2376' docker image inspect "$IMAGE" >/dev/null || {
  printf 'La imagen requerida no está disponible localmente; no se hará pull ni build.\n' >&2
  exit 1
}

if (( DRY_RUN )); then
  printf 'Validación previa completada. No se inicia agente ni contenedor.\n'
  exit 0
fi

# Ephemeral agent/identity dirs must be host-visible for dockerd bind mounts.
# PrivateTmp hides mktemp under /tmp from the Docker daemon → exit 125.
# Prefer systemd RuntimeDirectory, then StateDirectory; never rely on PrivateTmp /tmp.
EPHEMERAL_ROOT=""
if [[ -n "${RUNTIME_DIRECTORY:-}" && -d "$RUNTIME_DIRECTORY" && ! -L "$RUNTIME_DIRECTORY" ]]; then
  if [[ "${NYM_STAGING_OFFLINE:-0}" == "1" || -O "$RUNTIME_DIRECTORY" ]]; then
    EPHEMERAL_ROOT="$RUNTIME_DIRECTORY"
  fi
fi
if [[ -z "$EPHEMERAL_ROOT" && -d "$STATE" && ! -L "$STATE" ]]; then
  if [[ "${NYM_STAGING_OFFLINE:-0}" == "1" || -O "$STATE" ]]; then
    EPHEMERAL_ROOT="$STATE"
  fi
fi
[[ -n "$EPHEMERAL_ROOT" ]] || {
  printf 'Falta RuntimeDirectory/StateDirectory host-visible para agent.sock.\n' >&2
  exit 73
}
AGENT_DIR="$(mktemp -d -- "$EPHEMERAL_ROOT/agent.XXXXXX")"
IDENTITY_DIR="$(mktemp -d -- "$EPHEMERAL_ROOT/id.XXXXXX")"
chmod 700 "$AGENT_DIR" "$IDENTITY_DIR"
AGENT_SOCK="$AGENT_DIR/agent.sock"
CIDFILE="$IDENTITY_DIR/container.cid"
CONTAINER_NAME="nym-controller-${IDENTITY_DIR##*/}"
DOCKER_PID=''
cleanup() {
  local rc=$?
  trap - EXIT HUP INT TERM
  if [[ -f "$CIDFILE" && ! -L "$CIDFILE" ]]; then
    local cid
    cid="$(cat -- "$CIDFILE")"
    if [[ "$cid" =~ ^[a-f0-9]{64}$ ]]; then
      DOCKER_HOST='tcp://127.0.0.1:2376' timeout --kill-after=1s 8s docker stop --time 5 "$cid" >/dev/null 2>&1 || true
      DOCKER_HOST='tcp://127.0.0.1:2376' timeout --kill-after=1s 8s docker rm --force "$cid" >/dev/null 2>&1 || true
    fi
  fi
  if [[ -n "$DOCKER_PID" ]]; then
    kill -TERM "$DOCKER_PID" 2>/dev/null || true
  fi
  if [[ "${SSH_AGENT_PID:-}" =~ ^[0-9]+$ ]]; then
    ssh-agent -k >/dev/null 2>&1 || true
  fi
  rm -f -- "$AGENT_SOCK" "$IDENTITY_DIR/passwd" "$IDENTITY_DIR/group" "$CIDFILE"
  rmdir -- "$AGENT_DIR" "$IDENTITY_DIR" 2>/dev/null || true
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

AGENT_OUTPUT="$(ssh-agent -a "$AGENT_SOCK" -s)"
SSH_AGENT_PID="${AGENT_OUTPUT#*SSH_AGENT_PID=}"
SSH_AGENT_PID="${SSH_AGENT_PID%%;*}"
export SSH_AUTH_SOCK="$AGENT_SOCK" SSH_AGENT_PID
# ssh-agent sock is owned by nym-controller (host uid/gid 987). OpenSSH agent
# accepts the owner (and root) but resets non-owner peers even when DAC/ACL
# allow connect (uid 1001 → CONNECT_OK then Connection reset). Run the ansible
# container as the same uid/gid 987:987 so agent protocol works. Keep sock
# mode 600 (owner-only); AGENT_DIR stays 700. No chmod 666.
chmod 600 "$AGENT_SOCK"
sock_mode="$(stat -c '%a' -- "$AGENT_SOCK")"
other_digit="${sock_mode: -1}"
[[ "$other_digit" == 0 ]] || {
  printf 'agent.sock mode inseguro (other!=0): %s\n' "$sock_mode" >&2
  exit 73
}
ssh-add "$KEY" >/dev/null

printf 'nym-controller:x:987:987:Nym controller:/tmp:/bin/sh\n' > "$IDENTITY_DIR/passwd"
printf 'nym-controller:x:987:\n' > "$IDENTITY_DIR/group"
chmod 600 "$IDENTITY_DIR/passwd" "$IDENTITY_DIR/group"

DOCKER_HOST='tcp://127.0.0.1:2376' docker run --rm \
  --name "$CONTAINER_NAME" --cidfile "$CIDFILE" \
  --user 987:987 \
  --network hermes-default-net \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,exec,size=512m \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --cpus=1 \
  --memory=1536m \
  --pids-limit=64 \
  --mount "type=bind,src=$BASE,dst=/work,readonly" \
  --mount "type=bind,src=$AGENT_SOCK,dst=/run/ssh-agent.sock,readonly" \
  --mount "type=bind,src=$KNOWN_HOSTS,dst=/run/known_hosts,readonly" \
  --mount "type=bind,src=$IDENTITY_DIR/passwd,dst=/etc/passwd,readonly" \
  --mount "type=bind,src=$IDENTITY_DIR/group,dst=/etc/group,readonly" \
  --workdir /work \
  --env NYM_PLAYBOOK \
  --env NYM_LIMIT \
  --env NYM_UPDATE_VERSION \
  --env NYM_UPDATE_TAG \
  --env NYM_UPDATE_URL \
  --env NYM_UPDATE_SHA256 \
  --env HOME=/tmp \
  --env SSH_AUTH_SOCK=/run/ssh-agent.sock \
  --env PATH=/tmp/ansible/bin:/usr/local/bin:/usr/bin:/bin \
  "$IMAGE" \
  sh -c 'python3 -m venv /tmp/ansible && /tmp/ansible/bin/pip install --no-cache-dir ansible-core==2.19.11 && /tmp/ansible/bin/ansible-playbook -i inventory.ini "$NYM_PLAYBOOK" --limit "$NYM_LIMIT"' &
DOCKER_PID=$!
wait "$DOCKER_PID"
DOCKER_PID=''
