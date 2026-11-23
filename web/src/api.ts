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

export async function getGraph(stale: boolean): Promise<Graph> {
  const r = await fetch('/api/v1/graph?stale=' + stale)
  if (!r.ok) throw new Error('graph request failed: ' + r.status)
  return r.json()
}
