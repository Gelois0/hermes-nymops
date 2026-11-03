#!/usr/bin/env bash
# NYMOPS Alpha installer — check or install file layout only.
# Does NOT enable maintenance, contact Nym nodes, mount docker.sock, or write secrets.
set -Eeuo pipefail
umask 077

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
MODE="${1:-}"
[[ "$#" -eq 1 && ( "$MODE" == "--check" || "$MODE" == "--install" ) ]] || {
  printf 'Usage: %s --check|--install\n' "$0" >&2
  exit 2
}

need() { command -v "$1" >/dev/null || { printf 'Missing command: %s\n' "$1" >&2; exit 1; }; }
need python3
need install
need sha256sum

# Soft checks — versions vary by distro; Alpha does not claim universal pins.
python3 - <<'PY'
import sys
print(f"python: {sys.version.split()[0]}")
if sys.version_info < (3, 11):
    raise SystemExit("Python >= 3.11 required for this Alpha package")
PY

bash -n "$ROOT/src/nym-ansible/run-nym-ansible.sh"
python3 -m py_compile "$ROOT"/src/nym-mcp/*.py "$ROOT"/src/nym-controller/*.py

if [[ ! -f "$ROOT/src/nym-ansible/inventory.ini" ]]; then
  cp "$ROOT/src/nym-ansible/inventory.ini.example" "$ROOT/src/nym-ansible/inventory.ini"
  printf 'Created src/nym-ansible/inventory.ini from example (edit before live use).\n'
fi

if [[ "$MODE" == "--check" ]]; then
  printf 'CHECK PASS: syntax + inventory present. No system files written.\n'
  printf 'Next: review SECURITY.md / CONFIGURATION.md, then sudo %s --install\n' "$0"
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || { echo 'Run --install as root after explicit approval.' >&2; exit 1; }

for path in \
  /usr/local/libexec/nym-mcp \
  /usr/local/libexec/nym-controller \
  /etc/systemd/system/nym-controller.service \
  /etc/systemd/system/nym-controller.socket; do
  if [[ -e "$path" || -L "$path" ]]; then
    printf 'Refusing existing path: %s\n' "$path" >&2
    exit 1
  fi
done

mkdir -p /usr/local/libexec/nym-mcp /usr/local/libexec/nym-controller /etc/nym-controller
install -o root -g root -m 0644 "$ROOT"/src/nym-mcp/*.py /usr/local/libexec/nym-mcp/
install -o root -g root -m 0755 "$ROOT/deploy/nym-mcp-launcher" /usr/local/libexec/nym-mcp/nym-mcp-launcher

install -o root -g root -m 0644 "$ROOT"/src/nym-controller/*.py /usr/local/libexec/nym-controller/
install -o root -g root -m 0644 "$ROOT"/src/nym-ansible/*.yml /usr/local/libexec/nym-controller/
install -o root -g root -m 0644 "$ROOT/src/nym-ansible/ansible.cfg" /usr/local/libexec/nym-controller/
install -o root -g root -m 0644 "$ROOT/src/nym-ansible/inventory.ini" /usr/local/libexec/nym-controller/
install -o root -g root -m 0755 "$ROOT/src/nym-ansible/run-nym-ansible.sh" /usr/local/libexec/nym-controller/run-nym-ansible.sh
# requirements locks if present
for f in requirements-ansible-py313.in requirements-ansible-py313.lock; do
  [[ -f "$ROOT/src/nym-controller/$f" ]] && install -o root -g root -m 0644 "$ROOT/src/nym-controller/$f" /usr/local/libexec/nym-controller/
done
for f in requirements-mcp-py313.in requirements-mcp-py313.lock; do
  [[ -f "$ROOT/src/nym-mcp/$f" ]] && install -o root -g root -m 0644 "$ROOT/src/nym-mcp/$f" /usr/local/libexec/nym-mcp/
done

install -o root -g root -m 0644 "$ROOT/deploy/systemd/nym-controller.service" /etc/systemd/system/nym-controller.service
install -o root -g root -m 0644 "$ROOT/deploy/systemd/nym-controller.socket" /etc/systemd/system/nym-controller.socket
install -o root -g root -m 0644 "$ROOT/deploy/sysusers/nymops.conf" /usr/lib/sysusers.d/nymops.conf

if command -v systemd-sysusers >/dev/null; then
  systemd-sysusers /usr/lib/sysusers.d/nymops.conf || true
fi
if command -v systemd-analyze >/dev/null; then
  systemd-analyze verify /etc/systemd/system/nym-controller.service /etc/systemd/system/nym-controller.socket
fi
systemctl daemon-reload

printf '\nINSTALL FILES STAGED (Alpha). Units not enabled/started.\n'
printf 'Create /etc/nym-controller/{ssh_key,known_hosts} then approve enable/start separately.\n'
printf 'Keep NYM_MAINTENANCE_ENABLED unset. Do not mount docker.sock into Hermes.\n'
