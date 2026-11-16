#!/usr/bin/env bash
# Prepares a Docker host for Sporemap:
#   1. a read-only docker socket proxy on 127.0.0.1:2375 (never on the network)
#   2. optionally, a locked-down "sporemap" user whose key can ONLY forward to that port
#
# usage, as root on the docker host:
#   ./setup-docker-host.sh                                  # proxy only
#   ./setup-docker-host.sh "ssh-ed25519 AAAA... sporemap"   # proxy + ssh user
set -euo pipefail

PORT="${SPOREMAP_PROXY_PORT:-2375}"
NAME=dockerproxy
IMAGE=tecnativa/docker-socket-proxy
KEY="${1:-}"

say() { echo "== $*"; }
die() { echo "!! $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root"
command -v docker >/dev/null || die "docker isn't installed here"
if ! command -v curl >/dev/null; then
  say "installing curl"
  apt-get update -qq && apt-get install -y -qq curl >/dev/null
fi

# --- 1. the proxy -----------------------------------------------------------
if docker inspect "$NAME" >/dev/null 2>&1; then
  say "$NAME already exists, recreating it"
  docker rm -f "$NAME" >/dev/null
fi

say "starting $NAME on 127.0.0.1:$PORT (read-only)"
# POST defaults to 0 in this image, so every write is refused
docker run -d --name "$NAME" --restart unless-stopped \
  -p "127.0.0.1:$PORT:2375" \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e CONTAINERS=1 -e INFO=1 -e NETWORKS=1 \
  "$IMAGE" >/dev/null

for _ in $(seq 1 30); do
  curl -fs "127.0.0.1:$PORT/_ping" >/dev/null 2>&1 && break
  sleep 0.5
done

read_code=$(curl -s -o /dev/null -w "%{http_code}" "127.0.0.1:$PORT/containers/json")
write_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "127.0.0.1:$PORT/containers/create")
[ "$read_code" = "200" ] || die "reading containers failed (HTTP $read_code), check: docker logs $NAME"
if [ "$write_code" != "403" ]; then
  docker rm -f "$NAME" >/dev/null  # a writable proxy is worse than none
  die "writes were NOT blocked (HTTP $write_code), so the proxy was removed"
fi
say "proxy ok: reads 200, writes 403"

# --- 2. the ssh user (optional) ---------------------------------------------
if [ -z "$KEY" ]; then
  say "no public key given, skipping the ssh user"
  exit 0
fi
case "$KEY" in
  ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;;
  *) die "that doesn't look like a public key (should start with ssh-ed25519)" ;;
esac
command -v sshd >/dev/null || [ -x /usr/sbin/sshd ] || die "no ssh server here, install openssh-server first"

if ! id sporemap >/dev/null 2>&1; then
  say "creating user sporemap (no shell)"
  useradd --system --create-home --shell /usr/sbin/nologin sporemap
fi
install -d -m 700 -o sporemap -g sporemap /home/sporemap/.ssh
# restrict = no shell, pty, agent, x11 or forwarding. then allow forwarding to exactly one port.
echo "restrict,port-forwarding,permitopen=\"127.0.0.1:$PORT\" $KEY" > /home/sporemap/.ssh/authorized_keys
chown sporemap:sporemap /home/sporemap/.ssh/authorized_keys
chmod 600 /home/sporemap/.ssh/authorized_keys
say "ssh user ok: the key can only forward to 127.0.0.1:$PORT"

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "add this host to sporemap.yaml under docker.hosts:"
echo "  - name: $(hostname)"
echo "    ssh: ${ip:-THIS_HOST_IP}"
echo "    # jump: PROXMOX_HOST_IP   # if sporemap cant reach this ip directly (eg internal bridge)"
