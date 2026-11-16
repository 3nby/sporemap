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

export async function getGraph(stale: boolean): Promise<Graph> {
  const r = await fetch('/api/v1/graph?stale=' + stale)
  if (!r.ok) throw new Error('graph request failed: ' + r.status)
  return r.json()
}
