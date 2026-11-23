# Overview
Sporemap is a **WORK IN PROGRESS** homelab assistant that can automatically scan your lab and generate a graph model.


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

Left:
- Auth, so the UI can go beyond the LAN
- CI check that builds and typechecks `web/`
- Move the map layout (ELK) into a web worker
- Install docs, then tag v0.1.0 and publish to PyPI

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

# Web UI
The map lives in `web/` (Vite + React + React Flow + ELK). `npm run build` writes it into
`src/sporemap/ui/`, and `sporemap serve` hands it out at `/`.

```
cd web
npm install
npm run build     # then: sporemap serve, open http://127.0.0.1:8420
npm run dev       # hot reload on :5173, proxies /api to a running sporemap serve
```
