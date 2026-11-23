#!/usr/bin/env bash
# One command on a Proxmox host -> a running Sporemap CT that scans on a schedule.
#
# Makes an unprivileged Debian CT, installs Sporemap in it (lxc-setup.sh), then asks
# whether to set up read access for it:
#   1. a read-only Proxmox API token (sporemap@pve!lxc, PVEAuditor)
#   2. the cloudflared config reader (a locked-down "sporemap" user on this host)
#   3. the read-only docker socket proxy in every CT running docker
#   4. a Cloudflare API token you paste in
#   5. the url of Caddy's read-only admin proxy
# keys already set up for other machines (eg your PC) keep working. then it writes sporemap.yaml + sporemap.env in the CT so it all works with no hand edits.
#
# usage, as root on the proxmox host:
#   bash <(curl -fsSL https://raw.githubusercontent.com/3nby/sporemap/main/scripts/install-lxc.sh)
#   ./install-lxc.sh [--ctid N] [--ref BRANCH] [--storage local-lvm]
# rerun it with --ctid of the existing CT to update it or redo the access setup.
set -euo pipefail

RAW=https://raw.githubusercontent.com/3nby/sporemap
REF=main
ID=""
STORE=local-lvm
TSTORE=local  # where CT templates live
NAME=sporemap
KEYP=/var/lib/sporemap/.ssh/id_ed25519  # the CT's key, as the CT sees it
HERE=$(cd "$(dirname "$0")" && pwd)

say() { echo "== $*"; }
warn() { echo "!! $*" >&2; }
die() { echo "!! $*" >&2; exit 1; }
# /dev/tty so prompts still work when this script came in through a pipe
ask() { local v; read -r -p "$2 [$3]: " v </dev/tty || true; printf -v "$1" '%s' "${v:-$3}"; }
yes_no() { local v; read -r -p "$1 [y/N]: " v </dev/tty || true; [[ "$v" =~ ^[Yy] ]]; }
# pct exec without a stdin, so it cant hang or eat keystrokes meant for our prompts
px() { pct exec "$@" </dev/null; }
bip() { ip -4 -o addr show dev "$1" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ctid) ID=$2; shift ;;
    --ref) REF=$2; shift ;;
    --storage) STORE=$2; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "dont know $1, try --help" ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || die "run this as root"
command -v pct >/dev/null || die "pct not found, run this on the proxmox host"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"
CFG=$TMP/cfg  # what gets pushed into the CT at the end
mkdir "$CFG"

# use the script next to this one, or grab it from github when run through curl
fetch() {
  if [ -f "$HERE/$1" ]; then
    echo "$HERE/$1"
  else
    curl -fsSL "$RAW/$REF/scripts/$1" -o "$TMP/$1" || die "couldnt download $1"
    echo "$TMP/$1"
  fi
}

# --- the CT -----------------------------------------------------------------
NEW=0
if [ -n "$ID" ] && pct status "$ID" >/dev/null 2>&1; then
  say "CT $ID exists, updating it instead of making a new one"
  pct status "$ID" | grep -q running || pct start "$ID"
else
  NEW=1
  [ -n "$ID" ] || ID=$(pvesh get /cluster/nextid)
  ask ID "CT id" "$ID"
  ask NAME "hostname" "$NAME"
  ask STORE "storage for the CT disk" "$STORE"

  echo
  echo "bridges on this host:"
  for p in /sys/class/net/vmbr*; do
    b=${p##*/}
    echo "  $b  $(ip -4 -o addr show dev "$b" | awk '{print $4}' | head -1)"
  done
  echo "tip: put eth0 on the bridge your PC is on (to open the web UI) and, if your other"
  echo "     CTs sit on an internal bridge, eth1 on that one so sporemap reaches them directly."
  ask BR0 "eth0 bridge" "vmbr0"
  ask IP0 "eth0 ip (dhcp, or like 10.0.0.50/24)" "dhcp"
  NET0="name=eth0,bridge=$BR0,ip=$IP0"
  if [ "$IP0" != dhcp ]; then
    ask GW0 "eth0 gateway" ""
    [ -n "$GW0" ] && NET0="$NET0,gw=$GW0"
  fi
  ask BR1 "eth1 bridge (blank for none)" ""
  NET1=()
  if [ -n "$BR1" ]; then
    ask IP1 "eth1 ip (like 192.168.1.50/24, no gateway)" ""
    [ -n "$IP1" ] || die "eth1 needs an ip"
    NET1=(--net1 "name=eth1,bridge=$BR1,ip=$IP1")
  fi

  say "finding the newest debian template"
  pveam update >/dev/null || warn "pveam update failed, using the template list we have"
  # pveam lists arm64 builds too, and one of those wont start on an amd64 host
  arch=$(dpkg --print-architecture)
  T=$(pveam available --section system | awk '{print $2}' | grep -E "^debian-[0-9]+-standard_.*_$arch\.tar" | sort -V | tail -1)
  [ -n "$T" ] || die "no debian template for $arch found in pveam available"
  if ! pveam list "$TSTORE" | grep -q "$T"; then
    say "downloading $T"
    pveam download "$TSTORE" "$T" >/dev/null
  fi

  say "creating CT $ID ($NAME) from $T"
  pct create "$ID" "$TSTORE:vztmpl/$T" --hostname "$NAME" --unprivileged 1 --features nesting=1 \
    --cores 1 --memory 512 --swap 512 --rootfs "$STORE:4" --net0 "$NET0" "${NET1[@]}" \
    --onboot 1 --start 1 --description "Sporemap, made by install-lxc.sh. update: pct exec $ID -- /usr/local/bin/sporemap update" >/dev/null
  # pct create says nothing when --start fails, so check
  pct status "$ID" | grep -q running \
    || die "CT $ID was made but wont start. see why: pct start $ID --debug. clean up: pct destroy $ID"
fi

# bridges this CT is on, and this host's ip on the first of them (what the CT uses to reach us)
OURB=$(pct config "$ID" | awk -F'[=,]' '/^net[0-9]+:/ {for (i = 1; i <= NF; i++) if ($i == "bridge") print $(i + 1)}')
HOSTIP=""
for b in $OURB; do
  HOSTIP=$(bip "$b")
  [ -n "$HOSTIP" ] && break
done
[ -n "$HOSTIP" ] || warn "this host has no ip on the CT's bridges ($OURB), so the CT cant reach it for proxmox/cloudflared"
# echo "bridges: $OURB host ip: $HOSTIP"

say "waiting for the CT's network"
for _ in $(seq 1 30); do
  px "$ID" -- getent hosts github.com >/dev/null 2>&1 && break
  sleep 2
done
px "$ID" -- getent hosts github.com >/dev/null 2>&1 || die "CT $ID cant resolve github.com, check its network (pct enter $ID)"

say "installing sporemap in CT $ID (takes a minute or two)"
px "$ID" -- mkdir -p /opt/sporemap
pct push "$ID" "$(fetch lxc-setup.sh)" /opt/sporemap/lxc-setup.sh --perms 755
px "$ID" -- /opt/sporemap/lxc-setup.sh install "$REF"
PUB=$(px "$ID" -- cat "$KEYP.pub")

touch "$CFG/sources.yaml" "$CFG/sporemap.env" "$CFG/known_hosts"
chmod 600 "$CFG/sporemap.env"
scan_key() { ssh-keyscan -T 5 -t ed25519 "$1" 2>/dev/null >> "$CFG/known_hosts" || warn "couldnt read the ssh host key of $1"; }

# --- 1. proxmox token -------------------------------------------------------
echo
if yes_no "1/5 create a read-only Proxmox API token for it (sporemap@pve!lxc, PVEAuditor)?"; then
  if ! pvesh get "/access/users/sporemap@pve" >/dev/null 2>&1; then
    pveum user add sporemap@pve --comment "sporemap, read only"
  fi
  pveum acl modify / --users sporemap@pve --roles PVEAuditor
  go=1
  if pvesh get "/access/users/sporemap@pve/token/lxc" >/dev/null 2>&1; then
    # the secret is only shown once, so the only way to hand it to the CT is a new one
    if yes_no "    token sporemap@pve!lxc already exists. replace it (anything else using it stops working)?"; then
      pveum user token remove sporemap@pve lxc
    else
      go=0
      warn "kept the old token, set SPOREMAP_PROXMOX_TOKEN_SECRET in the CT's /etc/sporemap/sporemap.env by hand"
    fi
  fi
  if [ "$go" -eq 1 ]; then
    sec=$(pveum user token add sporemap@pve lxc --privsep 0 --comment "sporemap CT $ID" --output-format json \
      | python3 -c 'import json, sys; print(json.load(sys.stdin)["value"])')
    echo "SPOREMAP_PROXMOX_TOKEN_SECRET=$sec" >> "$CFG/sporemap.env"
    unset sec
    say "token made"
  fi
  {
    echo "  proxmox:"
    echo "    host: $HOSTIP"
    echo "    token_id: sporemap@pve!lxc"
    echo "    verify_ssl: false           # proxmox's own cert is self-signed"
  } >> "$CFG/sources.yaml"
fi

# --- 2. cloudflared reader --------------------------------------------------
CF=/etc/cloudflared/config.yml
HOSTKEY=0  # did we give the CT's key to this host's sporemap user
echo
if [ -f "$CF" ] && yes_no "2/5 let it read $CF over ssh (a sporemap user here whose key can only cat that file)?"; then
  if ! id sporemap >/dev/null 2>&1; then
    # a real shell, bc sshd runs the forced command through it
    useradd --system --create-home --shell /bin/sh sporemap
  fi
  runuser -u sporemap -- cat "$CF" >/dev/null 2>&1 || warn "user sporemap cant read $CF, fix its permissions"
  AK=/home/sporemap/.ssh/authorized_keys
  install -d -o sporemap -g sporemap -m 700 /home/sporemap/.ssh
  # same option style allow-ssh-jump.sh expects, so docker hops can be added to this line later
  line="command=\"cat $CF\",no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding $PUB"
  k=$(awk '{print $2}' <<<"$PUB")
  touch "$AK"
  if grep -qF "$k" "$AK"; then
    say "this CT's key is already allowed"
  else
    cp "$AK" "$AK.bak.$(date +%s)"
    if [ -s "$AK" ] && yes_no "    $AK already has $(grep -c . "$AK") key(s), eg your PC's. remove them? (no = keep them working too)"; then
      echo "$line" > "$AK"
    else
      echo "$line" >> "$AK"
    fi
    say "this CT's key can now read $CF"
  fi
  HOSTKEY=1
  chown sporemap:sporemap "$AK"
  chmod 600 "$AK"
  scan_key "$HOSTIP"
  {
    echo "  cloudflared:"
    echo "    host: $HOSTIP"
    echo "    key_path: $KEYP"
  } >> "$CFG/sources.yaml"
elif [ ! -f "$CF" ]; then
  echo "2/5 no $CF on this host, skipping the cloudflared reader"
fi

# --- 3. docker CTs ----------------------------------------------------------
echo
if yes_no "3/5 set up the read-only docker proxy in every CT running docker, and let this CT read them?"; then
  D=$TMP/docker
  mkdir "$D"
  for f in setup-proxmox-docker-cts.sh setup-docker-host.sh allow-ssh-jump.sh; do
    cp "$(fetch "$f")" "$D/$f"
  done
  bash "$D/setup-proxmox-docker-cts.sh" "$PUB" || warn "docker setup had problems, see above"

  hosts=""
  jumped=0
  for c in $(pct list | awk 'NR > 1 && $2 == "running" {print $1}'); do
    [ "$c" = "$ID" ] && continue
    pct config "$c" | grep -q '^unprivileged: 1' || continue
    px "$c" -- sh -c 'command -v docker >/dev/null 2>&1' || continue
    n=$(pct config "$c" | awk '/^hostname:/ {print $2}')
    cip=$(px "$c" -- sh -c "ip -4 -o addr show dev eth0 | awk '{print \$4}' | cut -d/ -f1 | head -1" 2>/dev/null || true)
    br=$(pct config "$c" | awk -F'[=,]' '/^net0:/ {for (i = 1; i <= NF; i++) if ($i == "bridge") print $(i + 1)}')
    [ -n "$cip" ] || { warn "CT $c ($n) has no eth0 ip, skipping"; continue; }
    hosts="$hosts      - name: $n"$'\n'"        ssh: $cip"$'\n'
    if ! grep -qx "$br" <<<"$OURB"; then
      # not on any of our bridges, so hop through this host
      hosts="$hosts        jump: $HOSTIP"$'\n'
      jumped=1
      [ "$HOSTKEY" -eq 1 ] || warn "$n needs a hop through this host, but step 2 was skipped so the CT's key cant log in here"
    fi
    scan_key "$cip"
  done
  if [ "$jumped" -eq 1 ]; then
    scan_key "$HOSTIP"  # ssh checks the hop's key too. lxc-setup.sh drops duplicates
  fi
  if [ -n "$hosts" ]; then
    {
      echo "  docker:"
      echo "    key_path: $KEYP"
      echo "    hosts:"
      printf '%s' "$hosts"
    } >> "$CFG/sources.yaml"
  fi
fi

# --- 4. cloudflare api ------------------------------------------------------
echo
echo "4/5 cloudflare: paste an API token with Zone:Zone:Read, Zone:DNS:Read and"
echo "    Account:Cloudflare Tunnel:Read (it wont show as you paste). blank skips this."
read -r -s -p "    token: " tok </dev/tty || true
echo
if [ -n "${tok:-}" ]; then
  echo "SPOREMAP_CLOUDFLARE_API_TOKEN=$tok" >> "$CFG/sporemap.env"
  echo "  cloudflare: {}" >> "$CFG/sources.yaml"
  say "cloudflare token saved for the CT"
fi
unset tok

# --- 5. caddy ---------------------------------------------------------------
echo
echo "5/5 caddy: the url of its read-only admin proxy, eg http://192.168.1.70:2020. blank skips this."
ask curl_ "    url" ""
if [ -n "$curl_" ]; then
  h=${curl_#*://}
  h=${h%%[:/]*}
  ask cip_ "    ip of the box caddy runs on" "$h"
  {
    echo "  caddy:"
    echo "    url: $curl_"
    if [ -n "$cip_" ]; then echo "    host_ip: $cip_"; fi
  } >> "$CFG/sources.yaml"
fi

# --- hand it all to the CT --------------------------------------------------
px "$ID" -- mkdir -p -m 700 /root/sporemap-cfg
for f in sources.yaml sporemap.env known_hosts; do
  pct push "$ID" "$CFG/$f" "/root/sporemap-cfg/$f" --perms 600
done
px "$ID" -- /opt/sporemap/lxc-setup.sh configure /root/sporemap-cfg

# --- web UI password ------------------------------------------------------------
echo
if px "$ID" -- grep -q '^SPOREMAP_PASSWORD_HASH=' /etc/sporemap/sporemap.env; then
  say "the web UI already has a password (change it: pct exec $ID -- sporemap set-password)"
elif ! yes_no "set a password for the web UI? (no = anyone on the LAN can see your whole lab)"; then
  warn "no password. set one later with: pct exec $ID -- /usr/local/bin/sporemap set-password"
else
  # not px: this one needs the terminal, it asks for the password twice
  pct exec "$ID" -- /usr/local/bin/sporemap set-password </dev/tty || warn "setting the password failed, try again with: pct exec $ID -- /usr/local/bin/sporemap set-password"
fi

echo
say "checking it"
px "$ID" -- /usr/local/bin/sporemap doctor || warn "doctor found problems, the fix: lines above say what to do"

CTIP=$(px "$ID" -- sh -c "ip -4 -o addr show dev eth0 | awk '{print \$4}' | cut -d/ -f1 | head -1" || true)
echo
if [ "$NEW" -eq 1 ]; then say "CT $ID is ready"; else say "CT $ID is updated"; fi
echo "   web UI:   http://${CTIP:-CT_IP}:8420   (LAN only, no login yet)"
echo "   update:   pct exec $ID -- /usr/local/bin/sporemap update"
echo "   logs:     pct exec $ID -- journalctl -u sporemap -f"
echo "   config:   pct exec $ID -- nano /etc/sporemap/sporemap.yaml   (then systemctl restart sporemap)"
