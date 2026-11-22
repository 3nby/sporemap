#!/usr/bin/env bash
# Lets this box's "sporemap" keys hop to other machines' SSH, for hosts that are
# only reachable through here (like CTs on an internal bridge). Every key line
# keeps everything else it had (like its forced command), so a PC key and the
# Sporemap CT's key can both live here.
#
# usage, as root on the jump box:
#   ./allow-ssh-jump.sh 192.168.1.10 [more ips...]
# each run REPLACES the allowed list on every key, so pass every ip you want allowed.
# setup-proxmox-docker-cts.sh calls this for you with all docker CTs.
set -euo pipefail

F="${SPOREMAP_AUTH_KEYS:-/home/sporemap/.ssh/authorized_keys}"

die() { echo "!! $*" >&2; exit 1; }
[ "$#" -ge 1 ] || die "give at least one ip to allow, eg: $0 192.168.1.10"
[ -s "$F" ] || die "no keys in $F, set up the sporemap user first"
for ip in "$@"; do
  [[ "$ip" =~ ^[0-9a-fA-F.:]+$ ]] || die "not an ip: $ip"
done

out=$(mktemp)
n=0
while IFS= read -r line; do
  if [ -z "${line// }" ] || [[ "$line" =~ ^[[:space:]]*# ]]; then
    echo "$line" >> "$out"  # keep blanks + comments as they are
    continue
  fi
  key=$(grep -oE '(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+) [A-Za-z0-9+/=]+( .*)?$' <<<"$line") \
    || { rm -f "$out"; die "couldnt find the key in this line of $F: ${line:0:60}..."; }
  opts=${line%"$key"}
  opts=${opts% }
  # drop the old forwarding rules, keep everything else (like the forced command)
  opts=$(sed -E 's/(^|,)no-port-forwarding//; s/(^|,)permitopen="[^"]*"//g; s/^,//' <<<"$opts")
  for ip in "$@"; do
    opts="$opts${opts:+,}permitopen=\"$ip:22\""
  done
  echo "$opts $key" >> "$out"
  n=$((n + 1))
done < "$F"

cp "$F" "$F.bak.$(date +%s)"
cat "$out" > "$F"  # cat, not mv, keeps the owner + perms
rm -f "$out"
echo "== $F: $n key(s) can now hop to ssh on: $*"
# cut -c1-120 "$F"
