import type { GNode } from './api'
import { ipsOf, parentOf, type Idx } from './facts'
import { str } from './fmt'

// the traffic paths through a thing: domain -> tunnel -> container and so on.
// the graph only says "tunnel routes to these 5 things", not which domain goes where, so
// when a path passes through a tunnel we read the tunnel's own routes table to stay on
// the right branch

export type Lit = { nodes: Set<number>; edges: Set<number>; paths: number[][] }

const MAX = 20 // paths, so a busy tunnel cant blow up the panel

function isTunnel(n: GNode | undefined): boolean {
  return !!n && n.kind === 'external' && n.attrs.type === 'tunnel'
}

// does a tunnel service like "http://192.168.1.10:8080" or "https://localhost:8006" land on t?
function lands(svc: string, t: GNode, tunnel: GNode, ix: Idx): boolean {
  let u: URL
  try {
    u = new URL(svc)
  } catch {
    return false // http_status:404 and friends
  }
  const port = Number(u.port || { 'http:': 80, 'https:': 443, 'ssh:': 22 }[u.protocol] || 0)
  let host = u.hostname
  if (host === 'localhost' || host === '127.0.0.1') {
    const box = parentOf(tunnel.id, ix) // localhost = the box cloudflared runs on
    if (!box) return false
    if (box.id === t.id) return true
    host = ipsOf(box, ix)[0] || ''
  }
  if (!ipsOf(t, ix).includes(host)) return false
  const a = t.attrs
  let ports: number[] = []
  if (t.kind === 'container') ports = ((a.ports as { host_port?: number }[]) || []).map((p) => Number(p.host_port)).filter(Boolean)
  if (t.kind === 'app') ports = ((a.listen as string[]) || []).map((l) => Number(l.split(':').pop()))
  return !ports.length || ports.includes(port)
}

// next hops forward. came = the node before this one (for picking a tunnel branch)
function fwd(id: number, came: number | undefined, ix: Idx): number[] {
  const all = (ix.out.get(id) || []).filter((e) => e.kind === 'routes_to').map((e) => e.dst)
  const me = ix.byId.get(id)
  const from = came === undefined ? undefined : ix.byId.get(came)
  if (!isTunnel(me) || !from || from.kind !== 'endpoint') return all
  const svc = str(((me!.attrs.routes as Record<string, string>) || {})[from.name])
  const hit = all.filter((d) => svc && lands(svc, ix.byId.get(d)!, me!, ix))
  return hit.length ? hit : all // cant tell? better too much than nothing
}

// next hops backward
function back(id: number, came: number | undefined, ix: Idx): number[] {
  const all = (ix.inn.get(id) || []).filter((e) => e.kind === 'routes_to').map((e) => e.src)
  const me = ix.byId.get(id)
  const to = came === undefined ? undefined : ix.byId.get(came)
  if (!isTunnel(me) || !to) return all
  const routes = (me!.attrs.routes as Record<string, string>) || {}
  const hit = all.filter((s) => {
    const e = ix.byId.get(s)
    return e && e.kind === 'endpoint' && routes[e.name] && lands(routes[e.name], to, me!, ix)
  })
  return hit.length ? hit : all
}

function walk(start: number, step: (id: number, came: number | undefined) => number[]): number[][] {
  const out: number[][] = []
  const go = (path: number[]) => {
    if (out.length >= MAX) return
    const cur = path[path.length - 1]
    const nxt = step(cur, path.length > 1 ? path[path.length - 2] : undefined).filter((n) => !path.includes(n))
    if (!nxt.length) {
      out.push(path)
      return
    }
    for (const n of nxt) go(path.concat(n))
  }
  go([start])
  return out
}

export function trace(id: number, ix: Idx): Lit {
  // a CT counts as everything inside it, so picking fileshare shows what reaches its containers
  const seeds: number[] = []
  const grab = (n: number) => {
    seeds.push(n)
    for (const k of ix.kids.get(n) || []) grab(k)
  }
  grab(id)

  const paths: number[][] = []
  for (const s of seeds) {
    const ups = walk(s, (n, c) => back(n, c, ix)).map((p) => p.reverse())
    for (const u of ups) {
      // walking down from s, the hop before s picks the branch when s is a tunnel,
      // so each way in gets its own way out
      const before = u.length > 1 ? u[u.length - 2] : undefined
      const downs = walk(s, (n, c) => fwd(n, c ?? (n === s ? before : undefined), ix))
      for (const d of downs) {
        const p = u.concat(d.slice(1))
        if (p.length > 1 && paths.length < MAX) paths.push(p)
      }
    }
  }

  const nodes = new Set<number>([id])
  const edges = new Set<number>()
  for (const p of paths) {
    for (let i = 0; i < p.length; i++) {
      nodes.add(p[i])
      if (i) {
        const e = (ix.out.get(p[i - 1]) || []).find((x) => x.kind === 'routes_to' && x.dst === p[i])
        if (e) edges.add(e.id)
      }
    }
  }
  // console.log('trace', id, paths)
  return { nodes, edges, paths }
}
