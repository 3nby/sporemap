# Overview
Sporemap is a **WORK IN PROGRESS** homelab assistant that can automatically scan your lab and generate a graph model.

It reads your Proxmox host, Docker hosts, Caddy, Cloudflare and cloudflared (all read-only), merges what they report into one graph, and shows it in a web UI: an overview of what needs a look, and a map of hosts, CTs, containers, domains and the traffic paths between them.


# Install

### On Proxmox (recommended)
Run this as root on the Proxmox host. It creates an unprivileged Debian CT with Sporemap in it, sets up read-only access to your sources, and starts scanning on a schedule. I do hope to break down this script piece by piece in the near future to make it easier to read and verify its safety.

```
bash <(curl -fsSL https://raw.githubusercontent.com/3nby/sporemap/main/scripts/install-lxc.sh)
```

It asks for:
- **CT id, hostname, storage**: the defaults are fine.
- **Network**: it lists your bridges. Put eth0 on the bridge your PC is on, so you can open the web UI. If your other CTs sit on an internal bridge, add eth1 on that bridge so Sporemap reaches them directly instead of through SSH hops.
- **Five optional steps**, each a yes or no:
  1. A read-only Proxmox API token (`sporemap@pve!lxc`, role PVEAuditor)
  2. The cloudflared config reader: a `sporemap` user on the host whose key can only read `/etc/cloudflared/config.yml`
  3. The read-only Docker socket proxy in every CT that runs Docker
  4. A Cloudflare API token (pasted in, it isn't shown)
  5. The URL of Caddy's read-only admin proxy (see Sources below)
- **A password for the web UI.** Say yes unless the CT is somewhere only you can reach, such as behind cloudflare access or tailscale. 

Keys already set up for other machines (like your PC) keep working. At the end it runs `sporemap doctor` and prints the address of the web UI. Rerun it with `--ctid <id>` to update an existing CT or redo the access setup.

### Anywhere else
On any Debian or Ubuntu box, as root, `scripts/lxc-setup.sh install` does the same install without the Proxmox parts (user, venv, config in `/etc/sporemap`, systemd service). Then add your sources to `/etc/sporemap/sporemap.yaml` by hand.

Or with pip, in a venv:
```
pip install sporemap            # from v0.1.0 on
cp sporemap.example.yaml sporemap.yaml
sporemap doctor
sporemap serve                  # http://127.0.0.1:8420
```
The pip package from PyPI includes the web UI. Installing straight from GitHub only includes it if `npm` is on the machine.


# Configuration
Sporemap looks for its config in this order: `SPOREMAP_CONFIG`, `./sporemap.yaml`, `/etc/sporemap/sporemap.yaml`. Secrets never go in the yaml. They go in `sporemap.env` next to it (or `.env`, or real environment variables), named `SPOREMAP_<SOURCE>_<FIELD>`, for example `SPOREMAP_PROXMOX_TOKEN_SECRET`.

| Setting | Default | What it does |
|---|---|---|
| `host` | `127.0.0.1` | Where `serve` listens. The Proxmox install sets `0.0.0.0` so your LAN can reach it. |
| `port` | `8420` | |
| `scan_every` | `15m` | How often `serve` scans each source (`30s`, `15m`, `2h`, `1d` or `off`). Any source can set its own. |
| `keep_scans` | `50` | Scans kept per source. Older ones are pruned. The latest good scan always stays. |
| `scan_timeout` | `120` | Seconds before one source's scan is given up on. |

After changing the config, restart the service (`systemctl restart sporemap` in the CT) and run `sporemap doctor`. `sporemap.example.yaml` shows every option.


# Sources
Every source only reads. Each section shows what Sporemap needs, and the Proxmox installer sets up the first four for you.

### Proxmox
An API token with the PVEAuditor role. The secret goes in `SPOREMAP_PROXMOX_TOKEN_SECRET`.
```yaml
proxmox:
  host: 192.168.1.10
  token_id: sporemap@pve!scan
  verify_ssl: false          # Proxmox's own certificate is self-signed
```

### Cloudflare
An API token with Zone:Zone:Read, Zone:DNS:Read and Account:Cloudflare Tunnel:Read, in `SPOREMAP_CLOUDFLARE_API_TOKEN`.
```yaml
cloudflare: {}               # or zones: [example.com] to limit it
```

### cloudflared (locally managed tunnels)
Read over SSH as a `sporemap` user whose key can only print the tunnel config. Its line in `~sporemap/.ssh/authorized_keys` looks like:
```
command="cat /etc/cloudflared/config.yml",no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding ssh-ed25519 AAAA... sporemap
```
```yaml
cloudflared:
  host: 192.168.1.10
  key_path: /var/lib/sporemap/.ssh/id_ed25519
```

### Docker
Each Docker host runs a read-only socket proxy on `127.0.0.1:2375` (writes are refused, the script checks that). Sporemap reaches it through an SSH user whose key can only forward to that one port. `scripts/setup-docker-host.sh` sets up one host, and `scripts/setup-proxmox-docker-cts.sh` does it for every CT on a Proxmox host.
```yaml
docker:
  key_path: /var/lib/sporemap/.ssh/id_ed25519
  hosts:
    - name: nas
      ssh: 192.168.1.20
    - name: media
      ssh: 10.0.0.30
      jump: 192.168.1.10     # only if Sporemap can't reach it directly
```

### Caddy
Sporemap reads Caddy's live config. Instead of opening the admin API, put a read-only proxy of it in your Caddyfile:
```
http://:2020 {
	@readonly {
		method GET
		path /config /config/*
	}
	handle @readonly {
		reverse_proxy localhost:2019 {
			header_up Host localhost:2019
		}
	}
	respond 403
}
```
```yaml
caddy:
  url: http://192.168.1.30:2020
  host_ip: 192.168.1.30      # the box Caddy runs on
```

### Demo
`demo: {}` adds a small fake lab, to try Sporemap with nothing connected. Remove it later with `sporemap sources forget demo`.


# Everyday use
In the Proxmox CT, run these as `pct exec <id> -- /usr/local/bin/sporemap <command>` from the host, or just `sporemap <command>` inside the CT. That wrapper runs them as the service user, so they never leave root-owned files behind.

| Command | What it does |
|---|---|
| `sporemap update` | Installs the latest build of main (with the web UI) and restarts the service. Needs root. |
| `sporemap doctor` | Checks the config, the database, the password and every source, with a fix for each problem. |
| `sporemap scan [source]` | Scans now instead of waiting for the schedule. The web UI's "scan now" does the same. |
| `sporemap set-password` | Sets the web UI password (`--off` removes it). Changing it signs everyone out. Needs root in the CT. |
| `sporemap prune` | Deletes old scans now and shrinks the database file. |
| `sporemap sources test <name>` | Checks one source's access, with a fix if it fails. |
| `sporemap sources forget <name>` | Deletes everything one source reported. |
| `sporemap config` | Shows which config and secret files are in use (secrets hidden). |

Logs: `journalctl -u sporemap -f` in the CT.


# Security
- Sporemap only reads. Every credential it gets is read-only: an auditor token, a Cloudflare read token, SSH keys locked to one command or one forwarded port, and a Docker proxy that refuses writes.
- The map describes your whole lab, which is useful to an attacker, so set a password (`sporemap set-password`). With one set, nothing but the login page and the health check answers until you sign in. Without one, the web UI and `doctor` warn you whenever Sporemap listens beyond localhost.
- Keep it on your LAN. If you ever expose it, put it behind HTTPS (a reverse proxy or a tunnel); the session cookie is marked secure when the proxy says the connection is HTTPS.
- Secrets live in `/etc/sporemap/sporemap.env`, readable by root and the service only. Only a hash of the password is stored.


# Graph Entries (Planned)
### Nodes:
- Devices, Hypervisors
- VMs/CTs
- apps, storage locations, shares
- networks, bridges, VLANs
- external connections
  - Cloudflare
  - Tailscale
  - etc.


### Edges:
- hosts
- runs
- mounts
- exposes
- routes_to
- member_of


# Roadmap
### V0.1: PoC
Discovery for Proxmox, Docker, Cloudflare, cloudflared and Caddy, merged into one graph.

Done:
- One-command Proxmox install (`scripts/install-lxc.sh`) that scans on a schedule
- `sporemap doctor`, scan retention, and an edge build that `sporemap update` installs
- Web UI: nested map with bridge lanes, inspector with traffic paths, search and filters, pins
- Overview page with health, problems worth a look, host capacity and sources
- One admin password for the web UI and API
- CI checks for Python and the web UI, and the map layout running in a web worker
- Install and usage docs

Left:
- Tag v0.1.0 and publish to PyPI

### V0.2: Network Additions
- LAN scan + fingerprinting
- VLANs
- trace path
- health checks
- Network page: every IP with its owner and MAC, free IPs per bridge, port map, conflicts
- Exposure page: every public hostname with its full path from Cloudflare to the service
- Changes page: what appeared, disappeared or changed between scans
- Notification management: pick what to be told about (something down, newly public, drift) and where (ntfy, Discord, webhook)
- Frontend design pass: a proper look for the whole site, beyond the current earthy theme

### Future
- Plan/Apply actions
- New-service wizard
- Backup wizard and quickview
- Inventory tables and export, a sources page, notes on any card

# Development
```
python -m venv .venv
.venv/Scripts/activate          # or: source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

The web UI lives in `web/` (Vite + React + React Flow + ELK). `npm run build` writes it into
`src/sporemap/ui/`, and `sporemap serve` hands it out at `/`.

```
cd web
npm install
npm run build     # then: sporemap serve, open http://127.0.0.1:8420
npm run dev       # hot reload on :5173, proxies /api to a running sporemap serve
```

To work on the UI against a real install, point the dev server at it: `SPOREMAP_API=http://<ct-ip>:8420 npm run dev` (in PowerShell: `$env:SPOREMAP_API = '...'; npm run dev`). If it has a password, sign in on the dev page.

Every push to main that passes CI publishes a wheel with the UI to the rolling `edge` release, which is what `sporemap update` installs.
