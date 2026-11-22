import { useCallback, useEffect, useMemo, useState } from 'react'
import { applyNodeChanges, Background, Controls, MarkerType, MiniMap, ReactFlow, type Edge, type Node } from '@xyflow/react'
import { getGraph, type GNode, type Graph } from './api'
import { H, H_CHIPS, TOP, TOP_CHIPS, TOP_LANE, W, layout, type Box } from './layout'
import { BoxNode, INK, KIND_COLOR, LaneNode, SporeNode, type SporeData } from './SporeNode'
import { buildView, type View } from './view'

const nodeTypes = { spore: SporeNode, box: BoxNode, lane: LaneNode }

// view -> nested boxes -> elk -> react flow nodes
async function place(v: View): Promise<Node<SporeData>[]> {
  const byId = new Map(v.nodes.map((n) => [n.id, n]))
  const box = (id: number): Box => {
    const k = v.kids.get(id) || []
    const c = (v.chips.get(id) || []).length > 0
    const top = byId.get(id)?.kind === 'lane' ? TOP_LANE : c ? TOP_CHIPS : TOP
    return { id: String(id), w: W, h: c ? H_CHIPS : H, top, kids: k.map(box) }
  }
  const roots = v.nodes.filter((n) => !v.parent.has(n.id)).map((n) => box(n.id))
  const links = v.edges.map((e) => ({ id: 'e' + e.id, source: String(e.src), target: String(e.dst) }))
  const placed = await layout(roots, links)
  return placed.map((p) => {
    const g = byId.get(Number(p.id)) as GNode
    const group = (v.kids.get(g.id) || []).length > 0
    return {
      id: p.id,
      type: g.kind === 'lane' ? 'lane' : group ? 'box' : 'spore',
      selectable: g.kind !== 'lane', // its just a backdrop
      // boxes cover most of the canvas, so dragging inside one has to pan the map, not
      // carry the whole host off. cards still drag (until pins are saved its just for a peek)
      draggable: !group,
      // boxes + lanes under the lines (zIndex 1000), cards over them, so lines never cross a card's text
      zIndex: group ? 0 : 2000,
      position: { x: p.x, y: p.y },
      parentId: p.parent,
      extent: p.parent ? ('parent' as const) : undefined, // cant drag a container out of its CT
      width: p.w, // known up front so fitView + the minimap dont wait on measuring
      height: p.h,
      style: group ? { width: p.w, height: p.h } : undefined,
      data: { g, chips: v.chips.get(g.id) || [] },
    }
  })
}

export function App() {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [nodes, setNodes] = useState<Node<SporeData>[]>([])
  const [stale, setStale] = useState(false)
  const [infra, setInfra] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState<GNode | null>(null)
  const [hover, setHover] = useState('')

  const load = useCallback(async () => {
    setBusy(true)
    setErr('')
    try {
      setGraph(await getGraph(stale))
    } catch (e) {
      setErr(String(e))
    }
    setBusy(false)
  }, [stale])

  useEffect(() => {
    load()
  }, [load])

  const view = useMemo(() => (graph ? buildView(graph, infra) : null), [graph, infra])

  useEffect(() => {
    if (!view) return
    let gone = false
    place(view)
      .then((ns) => {
        if (!gone) setNodes(ns)
      })
      .catch((e) => setErr('layout failed: ' + String(e)))
    return () => {
      gone = true // a newer layout is on its way, drop this one
    }
  }, [view])

  // labels only on the line under the mouse, or the ones touching the selected thing
  const edges: Edge[] = useMemo(() => {
    if (!view) return []
    return view.edges.map((e) => {
      const id = 'e' + e.id
      const near = sel !== null && (e.src === sel.id || e.dst === sel.id)
      return {
        id,
        source: String(e.src),
        target: String(e.dst),
        label: hover === id || near ? e.kind : undefined,
        className: 'edge-' + e.kind + (near ? ' near' : ''),
        // an arrow says which way traffic goes, no marching dashes
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: near ? INK.near : INK.line },
        zIndex: 1000, // or lines into a CT get drawn under its box
      }
    })
  }, [view, hover, sel])

  const kinds = useMemo(() => Array.from(new Set(nodes.map((n) => n.data.g.kind))).filter((k) => k !== 'lane').sort(), [nodes])

  return (
    <div className="app">
      <header>
        <b>Sporemap</b>
        <span className="muted">{(graph ? graph.nodes.length : 0) + ' things, ' + edges.length + ' links shown'}</span>
        <label>
          <input type="checkbox" checked={infra} onChange={(e) => setInfra(e.target.checked)} /> bridges + storage as nodes
        </label>
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
          onNodesChange={(ch) => setNodes((ns) => applyNodeChanges(ch, ns))}
          onNodeClick={(_, n) => setSel((n as Node<SporeData>).data.g)}
          onPaneClick={() => setSel(null)}
          onEdgeMouseEnter={(_, e) => setHover(e.id)}
          onEdgeMouseLeave={() => setHover('')}
          nodesConnectable={false}
          fitView
          minZoom={0.1}
        >
          <Background color={INK.dots} gap={22} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable maskColor="rgba(239, 233, 221, .7)"
            nodeColor={(n) => KIND_COLOR[(n as Node<SporeData>).data.g.kind] || INK.line} />
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
