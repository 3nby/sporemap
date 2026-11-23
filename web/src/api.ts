// shapes match sporemap/api/schemas.py
export type GNode = {
  id: number
  kind: string
  name: string
  attrs: Record<string, unknown>
  confidence: number
  stale: boolean
  first_seen: string
  last_seen: string
}

export type GEdge = {
  id: number
  kind: string
  src: number
  dst: number
  source: string
  confidence: number
}

export type Graph = { nodes: GNode[]; edges: GEdge[] }

export type SeenBy = { source: string; ext_id: string; name: string; seen_at: string }
export type NodeDetail = GNode & { seen_by: SeenBy[] }

// one node + which sources saw it. null when the backend is too old to have this
export async function getNode(id: number): Promise<NodeDetail | null> {
  const r = await fetch('/api/v1/nodes/' + id)
  if (!r.ok) return null
  return r.json()
}

export type Sched = {
  enabled: boolean
  running: string[]
  sources: { name: string; every: number | null; last_run: string | null; next_run: string | null }[]
}

export async function getSchedule(): Promise<Sched | null> {
  const r = await fetch('/api/v1/schedule')
  return r.ok ? r.json() : null
}

export type ScanResult = { name: string; scan: { status: string } | null; error: string | null }

// scans every configured source, waits until they're all done
export async function scanAll(): Promise<ScanResult[]> {
  const r = await fetch('/api/v1/scans/all', { method: 'POST' })
  if (!r.ok) throw new Error('scan failed: ' + r.status)
  return r.json()
}

export type Overview = {
  counts: Record<string, number>
  health: Record<'workloads' | 'containers' | 'tunnels' | 'sources', [number, number]>
  attention: { level: 'bad' | 'warn' | 'info'; what: string; text: string; node_id: number | null }[]
  sources: { name: string; status: string; error: string | null; at: string; last_ok: string | null }[]
  capacity: {
    id: number; name: string; cpus: number | null; mem: number | null
    cpus_running: number; mem_running: number; cpus_all: number; mem_all: number; running: number; total: number
  }[]
}

export async function getOverview(): Promise<Overview> {
  const r = await fetch('/api/v1/overview')
  if (!r.ok) throw new Error('overview request failed: ' + r.status)
  return r.json()
}

// a pinned spot, relative to the box it sits in. "in" says which box, so a pin from
// a box the thing has since moved out of gets ignored instead of landing somewhere odd
export type Pin = { x: number; y: number; in: string }

export async function getPins(): Promise<Map<number, Pin>> {
  const r = await fetch('/api/v1/overrides?field=pos')
  if (!r.ok) return new Map() // older backend, no pins
  const rows: { node_id: number; value: Pin }[] = await r.json()
  return new Map(rows.map((o) => [o.node_id, o.value]))
}

export async function setPin(id: number, p: Pin): Promise<void> {
  const r = await fetch('/api/v1/overrides/' + id + '/pos', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value: p }),
  })
  if (!r.ok) throw new Error('saving the pin failed: ' + r.status)
}

export async function dropPin(id: number | null): Promise<void> {
  // null = every pin
  const r = await fetch(id === null ? '/api/v1/overrides?field=pos' : '/api/v1/overrides/' + id + '/pos', { method: 'DELETE' })
  if (!r.ok) throw new Error('unpinning failed: ' + r.status)
}

export async function getGraph(stale: boolean): Promise<Graph> {
  const r = await fetch('/api/v1/graph?stale=' + stale)
  if (!r.ok) throw new Error('graph request failed: ' + r.status)
  return r.json()
}
