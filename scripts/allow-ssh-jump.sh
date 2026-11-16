#!/usr/bin/env bash
# Lets this box's existing "sporemap" key hop to other machines' SSH, for hosts
# that are only reachable through here (like CTs on an internal bridge).
# Everything else about the key stays as it was.
#
# usage, as root on the jump box:
#   ./allow-ssh-jump.sh 192.168.1.10 [more ips...]
# each run REPLACES the allowed list, so pass every ip you want allowed.
# setup-proxmox-docker-cts.sh calls this for you with all docker CTs.
set -euo pipefail

F="${SPOREMAP_AUTH_KEYS:-/home/sporemap/.ssh/authorized_keys}"

die() { echo "!! $*" >&2; exit 1; }
[ "$#" -ge 1 ] || die "give at least one ip to allow, eg: $0 192.168.1.10"
[ -f "$F" ] || die "no $F, set up the sporemap user first"
[ "$(grep -c . "$F")" -eq 1 ] || die "$F should have exactly one key line, fix it by hand"

line=$(grep . "$F")
key=$(grep -oE '(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+) [A-Za-z0-9+/=]+( .*)?$' <<<"$line") \
  || die "couldnt find the key in $F"
opts=${line%"$key"}
opts=${opts% }

# drop the old forwarding rules, keep everything else (like the forced command)
opts=$(sed -E 's/(^|,)no-port-forwarding//; s/(^|,)permitopen="[^"]*"//g; s/^,//' <<<"$opts")
for ip in "$@"; do
  [[ "$ip" =~ ^[0-9a-fA-F.:]+$ ]] || die "not an ip: $ip"
  opts="$opts${opts:+,}permitopen=\"$ip:22\""
done

cp "$F" "$F.bak.$(date +%s)"
echo "$opts $key" > "$F"
echo "== $F now allows ssh hops to: $*"
echo "   $(cut -c1-120 "$F")..."
