#!/usr/bin/env bash
# Installs or updates Sporemap on a Debian box: the CT install-lxc.sh makes, a VM, whatever.
#   - a "sporemap" system user, data in /var/lib/sporemap
#   - the code in /opt/sporemap (git checkout + venv), config in /etc/sporemap
#     on main it installs CI's edge wheel (has the web UI), other refs build from the checkout
#   - a systemd service that serves on 0.0.0.0:8420 and scans on a schedule
#   - a "sporemap" command that runs as the service user, plus "sporemap update"
#
# usage, as root:
#   ./lxc-setup.sh install [git ref]   # default main. safe to rerun, keeps config, secrets + data
#   ./lxc-setup.sh configure DIR       # install-lxc.sh uses this, merges DIR/{sources.yaml,sporemap.env,known_hosts}
set -euo pipefail

REPO="${SPOREMAP_REPO:-https://github.com/3nby/sporemap}"
APP=/opt/sporemap
VENV=$APP/venv
SRC=$APP/src
ETC=/etc/sporemap
DATA=/var/lib/sporemap
U=sporemap
KEY=$DATA/.ssh/id_ed25519
EDGE_API="${SPOREMAP_EDGE_API:-https://api.github.com/repos/3nby/sporemap/releases/tags/edge}"

say() { echo "== $*"; }
die() { echo "!! $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root"

# the yaml we start with. $1 = optional file with lines to put under sources:
write_yaml() {
  local f=$ETC/sporemap.yaml
  {
    echo "# sporemap config, made by lxc-setup.sh. see sporemap.example.yaml in the repo for every option"
    echo "# secrets go in sporemap.env next to this file, never in here"
    echo "# after changing it: systemctl restart sporemap, then: sporemap doctor"
    echo
    echo "host: 0.0.0.0              # LAN only, there is no login yet"
    echo "port: 8420"
    echo "data_dir: $DATA"
    echo "# scan_every: 15m"
    echo
    echo "sources:"
    if [ -n "${1:-}" ] && [ -s "$1" ]; then
      cat "$1"
    else
      echo "  # demo: {}"
    fi
  } > "$f"
  chown root:$U "$f"
  chmod 640 "$f"
}

# grabs the newest wheel from the rolling "edge" release (built by CI from main, has the
# web UI). sets WHL + EDGE_SHA. returns 1 if theres no wheel, then we build from source
edge_wheel() {
  local j url d
  j=$(curl -fsSL "$EDGE_API" 2>/dev/null) || { say "couldnt reach the edge release"; return 1; }
  # sha first: read eats leading spaces, so an empty url has to be the last field
  read -r EDGE_SHA url < <(python3 -c '
import json, re, sys
d = json.loads(sys.stdin.read())
w = [a["browser_download_url"] for a in d.get("assets", []) if a["name"].endswith(".whl")]
m = re.search(r"[0-9a-f]{40}", d.get("body") or "")
print(m.group(0) if m else "unknown", w[0] if w else "")
' <<<"$j") || return 1
  # echo "edge: $url $EDGE_SHA"
  [ -n "$url" ] || { say "the edge release has no wheel yet"; return 1; }
  d=$(mktemp -d)
  WHL=$d/${url##*/}  # pip wants the real wheel filename
  curl -fsSL -o "$WHL" "$url" || { rm -rf "$d"; say "couldnt download $url"; return 1; }
}

install_app() {
  local ref=${1:-main}

  say "installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv git ca-certificates openssh-client curl >/dev/null
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "sporemap needs python 3.11+, this box has $(python3 -V)"

  if ! id $U >/dev/null 2>&1; then
    say "creating user $U"
    useradd --system --home-dir $DATA --create-home --shell /usr/sbin/nologin $U
  fi
  install -d -o $U -g $U -m 750 $DATA
  install -d -m 755 $APP
  install -d -o root -g $U -m 750 $ETC

  say "fetching $REPO @ $ref"
  if [ ! -d $SRC/.git ]; then
    git init -q $SRC
    git -C $SRC remote add origin "$REPO"
  fi
  git -C $SRC fetch -q --depth 1 origin "$ref"
  git -C $SRC checkout -q --force FETCH_HEAD
  # git -C $SRC log -3 --oneline
  say "code is at $(git -C $SRC log -1 --format='%h %s')"
  # a newer version of this script came with the checkout? hand over to it right away
  if [ -z "${SPOREMAP_REEXEC:-}" ] && ! cmp -s $SRC/scripts/lxc-setup.sh "$0"; then
    install -m 755 $SRC/scripts/lxc-setup.sh $APP/lxc-setup.sh
    say "lxc-setup.sh changed, switching to the new one"
    SPOREMAP_REEXEC=1 exec $APP/lxc-setup.sh install "$ref"
  fi

  [ -x $VENV/bin/python ] || python3 -m venv $VENV
  $VENV/bin/pip install -q --upgrade pip
  if [ "$ref" = main ] && edge_wheel; then
    say "installing $(basename "$WHL") from the edge release (commit ${EDGE_SHA:0:7})"
    if [ "$EDGE_SHA" != "$(git -C $SRC rev-parse HEAD)" ]; then
      say "  that wheel is behind main, CI is probably still building it. run sporemap update again in a few minutes"
    fi
    # same version number every build, so force it in, then a normal install pulls new deps
    $VENV/bin/pip install -q --force-reinstall --no-deps "$WHL"
    $VENV/bin/pip install -q "$WHL"
    rm -rf "$(dirname "$WHL")"
  else
    say "building from the checkout, so no web UI (only the edge wheel from main and pypi releases have it)"
    # a local path always gets rebuilt, so this really updates even when the version didnt change
    $VENV/bin/pip install -q --upgrade $SRC
  fi
  install -m 755 $SRC/scripts/lxc-setup.sh $APP/lxc-setup.sh  # so the next update uses the newest version of this

  if [ ! -f $ETC/sporemap.yaml ]; then
    say "writing $ETC/sporemap.yaml"
    write_yaml
  fi
  if [ ! -f $ETC/sporemap.env ]; then
    echo "# secrets for sporemap, eg SPOREMAP_PROXMOX_TOKEN_SECRET=..." > $ETC/sporemap.env
  fi
  chown root:$U $ETC/sporemap.env
  chmod 640 $ETC/sporemap.env  # the service reads it, only root edits it

  if [ ! -f $KEY ]; then
    say "making the ssh key sporemap uses to read other boxes"
    install -d -o $U -g $U -m 700 $DATA/.ssh
    runuser -u $U -- ssh-keygen -q -t ed25519 -N "" -C "sporemap@$(hostname)" -f $KEY
  fi

  say "installing the sporemap command"
  cat > /usr/local/bin/sporemap <<'EOF'
#!/bin/sh
# made by lxc-setup.sh. runs the cli as the sporemap user, so hand-run commands
# never leave root-owned files in /var/lib/sporemap that the service cant write
if [ "$1" = update ]; then
  [ "$(id -u)" -eq 0 ] || { echo "run sporemap update as root" >&2; exit 1; }
  shift
  exec /opt/sporemap/lxc-setup.sh install "$@"
fi
# the password hash goes in /etc/sporemap/sporemap.env, which only root can write
if [ "$1" = set-password ]; then
  [ "$(id -u)" -eq 0 ] || { echo "run sporemap set-password as root" >&2; exit 1; }
  SPOREMAP_CONFIG=/etc/sporemap/sporemap.yaml /opt/sporemap/venv/bin/sporemap "$@" || exit $?
  systemctl try-restart sporemap && echo "restarted sporemap, sign in with the new password"
  exit 0
fi
cd /var/lib/sporemap || exit 1
export SPOREMAP_CONFIG=/etc/sporemap/sporemap.yaml
if [ "$(id -u)" -eq 0 ]; then
  exec runuser -u sporemap -- /opt/sporemap/venv/bin/sporemap "$@"
fi
exec /opt/sporemap/venv/bin/sporemap "$@"
EOF
  chmod 755 /usr/local/bin/sporemap

  say "installing the systemd service"
  cat > /etc/systemd/system/sporemap.service <<'EOF'
[Unit]
Description=Sporemap homelab mapper
After=network-online.target
Wants=network-online.target

[Service]
User=sporemap
Group=sporemap
WorkingDirectory=/var/lib/sporemap
Environment=SPOREMAP_CONFIG=/etc/sporemap/sporemap.yaml
ExecStart=/opt/sporemap/venv/bin/sporemap serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/sporemap

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q sporemap
  systemctl restart sporemap
  say "sporemap $($VENV/bin/sporemap version | awk '{print $2}') is running"
}

configure() {
  local d=${1:?give the dir with sources.yaml / sporemap.env / known_hosts}
  [ -d "$d" ] || die "no dir $d"

  if [ -s "$d/sporemap.env" ]; then
    # replace any line with the same name, keep the rest
    while IFS= read -r ln; do
      k=${ln%%=*}
      [[ "$k" =~ ^[A-Z0-9_]+$ ]] || continue
      sed -i "/^$k=/d" $ETC/sporemap.env
      echo "$ln" >> $ETC/sporemap.env
      say "set $k in $ETC/sporemap.env"
    done < "$d/sporemap.env"
  fi

  if [ -s "$d/known_hosts" ]; then
    install -d -o $U -g $U -m 700 $DATA/.ssh
    touch $DATA/.ssh/known_hosts
    while IFS= read -r ln; do
      grep -qxF "$ln" $DATA/.ssh/known_hosts || echo "$ln" >> $DATA/.ssh/known_hosts
    done < "$d/known_hosts"
    chown $U:$U $DATA/.ssh/known_hosts
    chmod 644 $DATA/.ssh/known_hosts
    say "trusted $(grep -c . "$d/known_hosts") ssh host keys"
  fi

  if [ -s "$d/sources.yaml" ]; then
    # only rewrite the yaml if nobody has configured sources yet, never clobber hand edits
    if $VENV/bin/python -c 'import sys, yaml; d = yaml.safe_load(open(sys.argv[1])) or {}; sys.exit(1 if d.get("sources") else 0)' $ETC/sporemap.yaml; then
      write_yaml "$d/sources.yaml"
      say "wrote the sources into $ETC/sporemap.yaml"
    else
      { echo "# sources from install-lxc.sh, copy what you want into sporemap.yaml under sources:"; cat "$d/sources.yaml"; } > $ETC/sporemap.yaml.suggested
      say "$ETC/sporemap.yaml already has sources, so the new ones went to $ETC/sporemap.yaml.suggested"
    fi
  fi
  rm -rf "$d"  # had a secret in it
  systemctl restart sporemap
}

case "${1:-install}" in
  install) install_app "${2:-main}" ;;
  configure) configure "${2:-}" ;;
  *) die "usage: $0 install [ref] | configure DIR" ;;
esac
