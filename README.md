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
Setting up discoveries for: proxmox, docker, cloudflare, caddy and basic graph implementation

### V0.2: Network Additions
- LAN scan + fingerprinting
- VLANs
- trace path
- health checks

### Future
- Alerts/Notifications
- Plan/Apply actions
- New-service wizard
- Backup wizard and quickview

# Web UI
The map lives in `web/` (Vite + React + React Flow + ELK). `npm run build` writes it into
`src/sporemap/ui/`, and `sporemap serve` hands it out at `/`.

```
cd web
npm install
npm run build     # then: sporemap serve, open http://127.0.0.1:8420
npm run dev       # hot reload on :5173, proxies /api to a running sporemap serve
```
