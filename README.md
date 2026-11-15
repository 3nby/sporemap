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