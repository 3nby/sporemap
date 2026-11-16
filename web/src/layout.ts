import ELK from 'elkjs/lib/elk.bundled.js'
import type { Edge, Node } from '@xyflow/react'

const elk = new ELK()

export const W = 190
export const H = 54

// left to right: internet stuff on the left, boxes and containers on the right
const opts = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.layered.spacing.nodeNodeBetweenLayers': '90',
  'elk.spacing.nodeNode': '28',
  'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
}

export async function layout(nodes: Node[], edges: Edge[]): Promise<Node[]> {
  const g = await elk.layout({
    id: 'root',
    layoutOptions: opts,
    children: nodes.map((n) => ({ id: n.id, width: W, height: H })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  })
  const pos: Record<string, { x: number; y: number }> = {}
  for (const c of g.children || []) pos[c.id] = { x: c.x || 0, y: c.y || 0 }
  return nodes.map((n) => ({ ...n, position: pos[n.id] || { x: 0, y: 0 } }))
}
