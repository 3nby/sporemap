import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import type { GNode } from './api'

export type SporeData = { g: GNode }

// placeholder colors, the real design comes later
export const KIND_COLOR: Record<string, string> = {
  host: '#6d8bd8',
  workload: '#59a985',
  container: '#3fb5b0',
  app: '#d8a24a',
  storage: '#9b7bd0',
  network: '#8a8f99',
  endpoint: '#d86d8b',
  external: '#b0b4bb',
}

export function SporeNode({ data, selected }: NodeProps<Node<SporeData>>) {
  const g = data.g
  const cls = 'spore' + (g.stale ? ' stale' : '') + (selected ? ' sel' : '') + (g.confidence < 1 ? ' guess' : '')
  return (
    <div className={cls} style={{ borderLeftColor: KIND_COLOR[g.kind] || '#999' }}>
      <Handle type="target" position={Position.Left} />
      <div className="kind">{g.kind}</div>
      <div className="name" title={g.name}>{g.name}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}
