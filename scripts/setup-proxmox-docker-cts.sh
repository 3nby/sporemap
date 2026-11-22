#!/usr/bin/env bash
# Run on a Proxmox host. Finds every running CT that has Docker installed and runs
# setup-docker-host.sh inside it (read-only proxy + optional locked-down ssh user).
# If this host has a sporemap user too, it also lets that key hop to each CT's ssh,
# so sporemap can reach CTs on internal bridges through here.
#
# usage, as root on the proxmox host, with setup-docker-host.sh next to this script:
#   ./setup-proxmox-docker-cts.sh --dry-run                   # just list the docker CTs
#   ./setup-proxmox-docker-cts.sh "ssh-ed25519 AAAA... sporemap"
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SETUP="$HERE/setup-docker-host.sh"
DRY=0
KEY=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    *) KEY="$a" ;;
  esac
done

die() { echo "!! $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "run this as root"
command -v pct >/dev/null || die "pct not found, run this on the proxmox host"
[ -f "$SETUP" ] || die "setup-docker-host.sh must be next to this script"

found=()
skipped_priv=()
for id in $(pct list | awk 'NR>1 && $2=="running" {print $1}'); do
  name=$(pct config "$id" | awk '/^hostname:/ {print $2}')
  # </dev/null on every pct exec, or it can sit there waiting on our stdin
  if ! pct exec "$id" -- sh -c 'command -v docker >/dev/null 2>&1' </dev/null; then
    echo "   CT $id ($name): no docker, skipping"
  elif ! pct config "$id" | grep -q '^unprivileged: 1'; then
    # root in a privileged CT is root on this host, and docker there usually cant
    # load its apparmor profile anyway. not worth loosening that to add a proxy.
    echo "!! CT $id ($name): docker found but the CT is PRIVILEGED, skipping"
    skipped_priv+=("$id")
  else
    echo "== CT $id ($name): docker found"
    found+=("$id")
  fi
done

[ "${#found[@]}" -gt 0 ] || { echo "no running CTs with docker"; exit 0; }
[ "$DRY" -eq 1 ] && { echo "dry run, would set up: ${found[*]}"; exit 0; }

failed=()
for id in "${found[@]}"; do
  echo
  echo "########## CT $id"
  pct push "$id" "$SETUP" /root/setup-docker-host.sh --perms 755
  if ! pct exec "$id" -- /root/setup-docker-host.sh "$KEY" </dev/null; then
    failed+=("$id")
  fi
done

echo
echo "########## summary"
ips=()
for id in "${found[@]}"; do
  # eth0 on purpose, hostname -I can list docker0 (172.17.x) first
  ip=$(pct exec "$id" -- sh -c "ip -4 -o addr show dev eth0 | awk '{print \$4}' | cut -d/ -f1 | head -1" </dev/null 2>/dev/null || true)
  name=$(pct config "$id" | awk '/^hostname:/ {print $2}')
  echo "CT $id  $name  ${ip:-?}"
  [ -n "$ip" ] && ips+=("$ip")
done

# let this host's sporemap key hop to every docker CT's ssh (only port 22, only these ips)
JUMP="$HERE/allow-ssh-jump.sh"
if [ -n "$KEY" ] && [ "${#ips[@]}" -gt 0 ]; then
  if [ -f /home/sporemap/.ssh/authorized_keys ] && [ -f "$JUMP" ]; then
    echo
    bash "$JUMP" "${ips[@]}"
  else
    echo
    echo "note: no sporemap user on this host (or allow-ssh-jump.sh missing), so no ssh hops were set up."
    echo "      CTs on internal bridges wont be reachable until you run: ./allow-ssh-jump.sh ${ips[*]}"
  fi
fi

if [ "${#skipped_priv[@]}" -gt 0 ]; then
  echo
  echo "skipped privileged CTs: ${skipped_priv[*]}"
  echo "  to include them, convert to unprivileged (backup, then restore with --unprivileged 1)."
fi
if [ "${#failed[@]}" -gt 0 ]; then
  die "setup failed in: ${failed[*]} (see the output above)"
fi
echo "all done."
