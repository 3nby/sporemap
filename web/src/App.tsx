import { useCallback, useEffect, useMemo, useState } from 'react'
import { Background, Controls, MiniMap, ReactFlow, type Edge, type Node } from '@xyflow/react'
import { getGraph, type GNode, type Graph } from './api'
import { H, W, layout } from './layout'
import { KIND_COLOR, SporeNode, type SporeData } from './SporeNode'

const nodeTypes = { spore: SporeNode }

function toFlow(g: Graph): { nodes: Node<SporeData>[]; edges: Edge[] } {
  const nodes = g.nodes.map((n) => ({
    id: String(n.id),
    type: 'spore',
    position: { x: 0, y: 0 },
    width: W, // known up front so fitView + the minimap dont wait on measuring
    height: H,
    data: { g: n },
  }))
  const edges = g.edges.map((e) => ({
    id: 'e' + e.id,
    source: String(e.src),
    target: String(e.dst),
    label: e.kind,
    className: 'edge-' + e.kind,
    animated: e.kind === 'routes_to', // traffic flows, the rest is structure
  }))
  return { nodes, edges }
}

export function App() {
  const [nodes, setNodes] = useState<Node<SporeData>[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [stale, setStale] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState<GNode | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    setErr('')
    try {
      const f = toFlow(await getGraph(stale))
      setNodes((await layout(f.nodes, f.edges)) as Node<SporeData>[])
      setEdges(f.edges)
      // console.log('graph', f.nodes.length, f.edges.length)
    } catch (e) {
      setErr(String(e))
    }
    setBusy(false)
  }, [stale])

  useEffect(() => {
    load()
  }, [load])

  const kinds = useMemo(() => Array.from(new Set(nodes.map((n) => n.data.g.kind))).sort(), [nodes])

  return (
    <div className="app">
      <header>
        <b>Sporemap</b>
        <span className="muted">{nodes.length + ' nodes, ' + edges.length + ' edges'}</span>
        <label>
          <input type="checkbox" checked={stale} onChange={(e) => setStale(e.target.checked)} /> show stale
        </label>
        <button onClick={load} disabled={busy}>{busy ? 'loading...' : 'reload'}</button>
        <span className="legend">
          {kinds.map((k) => (
            <span key={k}><i style={{ background: KIND_COLOR[k] || '#999' }} />{k}</span>
          ))}
        </span>
      </header>
      {err && <div className="err">{err}</div>}
      <main>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={(_, n) => setSel((n as Node<SporeData>).data.g)}
          onPaneClick={() => setSel(null)}
          nodesConnectable={false}
          fitView
          minZoom={0.1}
        >
          <Background />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable nodeColor={(n) => KIND_COLOR[(n as Node<SporeData>).data.g.kind] || '#999'} />
        </ReactFlow>
        {sel && (
          <aside>
            <button className="x" onClick={() => setSel(null)}>x</button>
            <div className="kind">{sel.kind + (sel.stale ? ' (stale)' : '')}</div>
            <h2>{sel.name}</h2>
            <div className="muted">{'id ' + sel.id + ', confidence ' + sel.confidence}</div>
            <div className="muted">{'last seen ' + new Date(sel.last_seen).toLocaleString()}</div>
            <pre>{JSON.stringify(sel.attrs, null, 2)}</pre>
          </aside>
        )}
      </main>
    </div>
  )
}
