import type { GEdge, GNode, Graph } from './api'
import { bytes, str } from './fmt'

// lookups over the full graph (every edge, not just the drawn ones)
export type Idx = {
  byId: Map<number, GNode>
  out: Map<number, GEdge[]>
  inn: Map<number, GEdge[]>
  parent: Map<number, number> // via hosts/runs
  kids: Map<number, number[]>
}

export function index(g: Graph): Idx {
  const ix: Idx = { byId: new Map(g.nodes.map((n) => [n.id, n])), out: new Map(), inn: new Map(), parent: new Map(), kids: new Map() }
  for (const e of g.edges) {
    ix.out.set(e.src, (ix.out.get(e.src) || []).concat(e))
    ix.inn.set(e.dst, (ix.inn.get(e.dst) || []).concat(e))
    if ((e.kind === 'hosts' || e.kind === 'runs') && !ix.parent.has(e.dst)) {
      ix.parent.set(e.dst, e.src)
      ix.kids.set(e.src, (ix.kids.get(e.src) || []).concat(e.dst))
    }
  }
  return ix
}

export function parentOf(id: number, ix: Idx): GNode | undefined {
  const p = ix.parent.get(id)
  return p === undefined ? undefined : ix.byId.get(p)
}

// every ip a thing answers on. containers + apps answer on their CT's ips
export function ipsOf(n: GNode, ix: Idx): string[] {
  const a = n.attrs
  if (n.kind === 'host') return a.ip ? [str(a.ip)] : []
  if (n.kind === 'workload') return ((a.ips as string[]) || []).map(String)
  if (n.kind === 'container' || n.kind === 'app') {
    const p = parentOf(n.id, ix)
    return p ? ipsOf(p, ix) : []
  }
  return []
}

export type Fact = { k: string; v: string; href?: string }

// the status word for the pill, or ''
export function statusOf(n: GNode): string {
  const a = n.attrs
  if (n.kind === 'container') return str(a.state)
  if (n.kind === 'external' && a.placeholder) return 'unknown'
  return str(a.status)
}

// what the inspector (and the first few rows of the hover card) show, per kind
export function facts(n: GNode, ix: Idx): Fact[] {
  const a = n.attrs
  const f: Fact[] = []
  const add = (k: string, v: unknown, href?: string) => {
    if (str(v) !== '') f.push({ k, v: str(v), href })
  }

  if (n.kind === 'host') {
    add('ip', a.ip)
    add('cpus', a.cpus ? a.cpus + ' cores' : '')
    add('memory', bytes(a.mem))
    add('platform', a.platform)
    if (a.ip && a.platform === 'proxmox') add('web ui', 'open Proxmox', 'https://' + a.ip + ':8006')
  } else if (n.kind === 'workload') {
    for (const nic of (a.nics as { dev?: string; bridge?: string; ip?: string; mac?: string }[]) || []) {
      add(str(nic.dev) || 'nic', [nic.bridge, nic.ip || 'dhcp', nic.mac].filter(Boolean).join(' · '))
    }
    if (!a.nics) add('ips', ((a.ips as string[]) || []).join(', '))
    add('cpus', a.cpus ? a.cpus + ' cores' : '')
    add('memory', bytes(a.mem))
    add('disk', bytes(a.disk))
    add('type', (a.type === 'qemu' ? 'VM ' : 'LXC ') + str(a.vmid) + (a.node ? ' on ' + a.node : ''))
    const h = parentOf(n.id, ix)
    if (h && h.attrs.ip && a.vmid) add('proxmox', 'open in Proxmox', 'https://' + h.attrs.ip + ':8006/#v1:0:=' + (a.type === 'qemu' ? 'qemu' : 'lxc') + '%2F' + a.vmid)
  } else if (n.kind === 'container') {
    const ip = ipsOf(n, ix)[0]
    for (const p of (a.ports as { host_port?: number; port: number; proto?: string }[]) || []) {
      const v = (p.host_port ? p.host_port + ' → ' : '') + p.port + '/' + (p.proto || 'tcp')
      add('port', v, p.host_port && ip && p.proto !== 'udp' ? 'http://' + ip + ':' + p.host_port : undefined)
    }
    add('status', a.status)
    add('image', a.image)
    add('compose', [a.compose_project, a.compose_service].filter(Boolean).join(' / '))
    add('networks', ((a.networks as string[]) || []).join(', '))
  } else if (n.kind === 'app') {
    add('listens', ((a.listen as string[]) || []).join(', '))
    for (const [d, ups] of Object.entries((a.sites as Record<string, string[]>) || {})) {
      add('site', d + ' → ' + (ups || []).map((u) => u.replace(/^ip:/, '')).join(', '))
    }
  } else if (n.kind === 'endpoint') {
    add('record', [a.type, a.content].filter(Boolean).join(' → '))
    if (a.proxied !== undefined) add('proxied', a.proxied ? 'yes, through Cloudflare' : 'no, DNS only')
    add('zone', a.zone)
    if (/^[a-z0-9.-]+$/i.test(n.name) && n.name.includes('.')) add('open', 'https://' + n.name, 'https://' + n.name)
  } else if (n.kind === 'external' && a.type === 'tunnel') {
    add('status', [a.status, a.connections !== undefined ? a.connections + ' connections' : ''].filter(Boolean).join(', '))
    for (const [h, s] of Object.entries((a.routes as Record<string, string>) || {})) add('route', h + ' → ' + s)
    add('provider', a.provider)
  } else if (n.kind === 'external' && a.placeholder) {
    add('note', 'something points here, but no source knows what it is')
  } else if (n.kind === 'storage') {
    add('type', a.type)
    add('size', bytes(a.size))
    add('content', a.content)
    add('shared', a.shared === undefined ? '' : a.shared ? 'yes' : 'no')
  } else if (n.kind === 'network') {
    add('cidr', a.cidr)
    add('active', a.active === undefined ? '' : a.active ? 'yes' : 'no')
  }
  return f
}
