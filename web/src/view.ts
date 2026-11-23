import type { GEdge, GNode, Graph } from './api'

// what the map shows, worked out from the raw graph before any layout happens

export type Chip = { kind: string; name: string }

export type View = {
  nodes: GNode[] // the ones that get drawn
  parent: Map<number, number> // child id -> the box it sits inside
  kids: Map<number, number[]>
  chips: Map<number, Chip[]> // bridges + storage as little tags on a card
  edges: GEdge[] // lines that get drawn
}

// these mean "lives inside", so they become nesting instead of lines
const NEST = new Set(['hosts', 'runs'])
// these point at bridges and storage nearly everything shares, so they turn into chips
const CHIP = new Set(['member_of', 'mounts'])
const INFRA = new Set(['network', 'storage'])

// hide = kinds switched off in the toolbar
export function buildView(g: Graph, infra: boolean, hide: Set<string> = new Set()): View {
  const byId = new Map(g.nodes.map((n) => [n.id, n]))
  const nodes = g.nodes.filter((n) => (infra || !INFRA.has(n.kind)) && !hide.has(n.kind))
  const shown = new Set(nodes.map((n) => n.id))

  const parent = new Map<number, number>()
  const kids = new Map<number, number[]>()
  const chips = new Map<number, Chip[]>()
  const edges: GEdge[] = []

  // who sits inside what, over everything, then each shown thing goes in the nearest shown
  // box above it (hide CTs and their containers sit straight in the host)
  const raw = new Map<number, number>()
  for (const e of g.edges) {
    if (NEST.has(e.kind) && !raw.has(e.dst) && !_loops(raw, e.src, e.dst)) raw.set(e.dst, e.src)
  }
  for (const n of nodes) {
    let p = raw.get(n.id)
    while (p !== undefined && !shown.has(p)) p = raw.get(p)
    if (p !== undefined) {
      parent.set(n.id, p)
      kids.set(p, (kids.get(p) || []).concat(n.id))
    }
  }

  for (const e of g.edges) {
    if (NEST.has(e.kind) || !shown.has(e.src)) continue
    if (CHIP.has(e.kind) && !infra) {
      const d = byId.get(e.dst)
      if (d) chips.set(e.src, (chips.get(e.src) || []).concat({ kind: d.kind, name: d.name }))
    } else if (shown.has(e.src) && shown.has(e.dst)) {
      edges.push(e)
    }
  }
  if (!infra) _lanes(g, nodes, parent, kids, chips)
  // console.log('view', nodes.length, edges.length, parent.size)
  return { nodes, parent, kids, chips, edges }
}

// inside a host, sort its CTs into see-through lanes by the bridges they sit on
// (vmbr0, vmbr2, or both), so the bridge is a place on the map instead of a chip on every card.
// lanes are made-up nodes with negative ids and kind "lane"
function _lanes(g: Graph, nodes: GNode[], parent: Map<number, number>, kids: Map<number, number[]>, chips: Map<number, Chip[]>) {
  const net = new Map(g.nodes.filter((n) => n.kind === 'network').map((n) => [n.name, n]))
  const byId = new Map(nodes.map((n) => [n.id, n]))
  let next = -1
  for (const h of nodes.filter((n) => n.kind === 'host')) {
    const lanes = new Map<string, number[]>()
    const keep: number[] = []
    for (const k of kids.get(h.id) || []) {
      const bs = (chips.get(k) || []).filter((c) => c.kind === 'network').map((c) => c.name).sort()
      if (byId.get(k)?.kind !== 'workload' || !bs.length) {
        keep.push(k)
        continue
      }
      const key = bs.join(' + ')
      lanes.set(key, (lanes.get(key) || []).concat(k))
      chips.set(k, (chips.get(k) || []).filter((c) => c.kind !== 'network')) // the lane says it now
    }
    if (!lanes.size) continue
    chips.set(h.id, (chips.get(h.id) || []).filter((c) => c.kind !== 'network'))
    // single bridges first, the "both" lanes after
    const keys = Array.from(lanes.keys()).sort((a, b) => a.split(' + ').length - b.split(' + ').length || a.localeCompare(b))
    const ids: number[] = []
    for (const key of keys) {
      const id = next--
      const nets = key.split(' + ').map((b) => ({ name: b, cidr: String(net.get(b)?.attrs.cidr ?? net.get(b)?.attrs.address ?? '') }))
      nodes.push({ id, kind: 'lane', name: key, attrs: { nets }, confidence: 1, stale: false, first_seen: '', last_seen: '' })
      parent.set(id, h.id)
      for (const w of lanes.get(key) || []) parent.set(w, id)
      kids.set(id, lanes.get(key) || [])
      ids.push(id)
    }
    kids.set(h.id, keep.concat(ids))
  }
}

// would making dst a child of src put src inside itself?
function _loops(parent: Map<number, number>, src: number, dst: number): boolean {
  let p: number | undefined = src
  while (p !== undefined) {
    if (p === dst) return true
    p = parent.get(p)
  }
  return false
}

// stopped CTs, exited containers, dead tunnels
export function isOff(n: GNode): boolean {
  const a = n.attrs
  if (n.kind === 'workload' || n.kind === 'host') return a.status !== undefined && a.status !== 'running' && a.status !== 'online'
  if (n.kind === 'container') return a.state !== undefined && a.state !== 'running'
  if (n.kind === 'external' && a.type === 'tunnel') return a.status === 'down' || a.status === 'inactive'
  return false
}

// what to call it on the card. tunnels are "external" in the data but deserve their own name
export function kindLabel(n: GNode): string {
  if (n.kind === 'lane') return 'bridge'
  if (n.kind === 'external' && n.attrs.type === 'tunnel') return 'tunnel'
  if (n.kind === 'external' && n.attrs.placeholder) return 'unknown'
  if (n.kind === 'workload' && n.attrs.type) return String(n.attrs.type) + ' ' + String(n.attrs.vmid ?? '')
  return n.kind
}
