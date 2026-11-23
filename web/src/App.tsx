import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  applyNodeChanges, Background, Controls, MarkerType, MiniMap, ReactFlow,
  type Edge, type Node, type ReactFlowInstance,
} from '@xyflow/react'
import { dropPin, getGraph, getPins, getSchedule, scanAll, setPin, type GNode, type Graph, type Pin, type Sched } from './api'
import { index } from './facts'
import { HoverCard } from './HoverCard'
import { Inspector } from './Inspector'
import { H, H_CHIPS, TOP, TOP_CHIPS, TOP_LANE, W, layout, type Box } from './layout'
import { BoxNode, INK, KIND_COLOR, LaneNode, SporeNode, type SporeData } from './SporeNode'
import { Toolbar } from './Toolbar'
import { trace } from './trace'
import { buildView, type View } from './view'

const nodeTypes = { spore: SporeNode, box: BoxNode, lane: LaneNode }

// kind chips go in the order traffic flows, internet first
const ORDER = ['endpoint', 'external', 'app', 'host', 'workload', 'container', 'network', 'storage']

// hidden kinds are a per-browser nicety, so storage failing (private window etc) is fine
function loadHide(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem('sporemap.hide') || '[]') as string[])
  } catch {
    return new Set()
  }
}

function saveHide(h: Set<string>) {
  try {
    localStorage.setItem('sporemap.hide', JSON.stringify(Array.from(h)))
  } catch {
    // nowhere to keep it, it just resets next visit
  }
}

// which box a thing sits in, as a string that survives reloads. lanes have made-up ids
// that can shift, so they go by host + bridge names instead
export function boxRef(id: number, v: View): string {
  const p = v.parent.get(id)
  if (p === undefined) return ''
  if (p < 0) {
    const lane = v.nodes.find((n) => n.id === p)
    return 'lane:' + (v.parent.get(p) ?? '') + ':' + (lane ? lane.name : '')
  }
  return String(p)
}

// view -> nested boxes -> elk -> pins on top -> react flow nodes
async function place(v: View, pins: Map<number, Pin>): Promise<Node<SporeData>[]> {
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

  // pinned things go where you left them, as long as theyre still in the same box
  const pinned = new Set<string>()
  for (const p of placed) {
    const pin = pins.get(Number(p.id))
    if (pin && pin.in === boxRef(Number(p.id), v)) {
      p.x = pin.x
      p.y = pin.y
      pinned.add(p.id)
    }
  }
  // then every box grows to hold its kids. placed lists parents first, so walking it
  // backwards does the innermost boxes first and their growth carries up
  const byP = new Map(placed.map((p) => [p.id, p]))
  for (const p of placed.slice().reverse()) {
    const par = p.parent ? byP.get(p.parent) : undefined
    if (!par) continue
    par.w = Math.max(par.w, p.x + p.w + 14)
    par.h = Math.max(par.h, p.y + p.h + 14)
  }

  return placed.map((p) => {
    const g = byId.get(Number(p.id)) as GNode
    const group = (v.kids.get(g.id) || []).length > 0
    return {
      id: p.id,
      type: g.kind === 'lane' ? 'lane' : group ? 'box' : 'spore',
      selectable: g.kind !== 'lane', // its just a backdrop
      // boxes cover most of the canvas, so dragging inside one has to pan the map. they
      // only move when you grab their header
      draggable: g.kind !== 'lane',
      dragHandle: group ? '.head' : undefined,
      // boxes + lanes under the lines (zIndex 1000), cards over them, so lines never cross a card's text
      zIndex: group ? 0 : 2000,
      position: { x: p.x, y: p.y },
      parentId: p.parent,
      extent: p.parent ? ('parent' as const) : undefined, // cant drag a container out of its CT
      width: p.w, // known up front so fitView + the minimap dont wait on measuring
      height: p.h,
      style: group ? { width: p.w, height: p.h } : undefined,
      data: { g, chips: v.chips.get(g.id) || [], pinned: pinned.has(p.id) },
    }
  })
}

export function App() {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [pins, setPins] = useState<Map<number, Pin>>(new Map())
  const [nodes, setNodes] = useState<Node<SporeData>[]>([])
  const [stale, setStale] = useState(false)
  const [infra, setInfra] = useState(false)
  const [err, setErr] = useState('')
  const [sel, setSel] = useState<GNode | null>(null)
  const [hide, setHide] = useState<Set<string>>(loadHide)
  const [sched, setSched] = useState<Sched | null>(null)
  const [scanning, setScanning] = useState(false)
  const [hover, setHover] = useState('') // edge under the mouse
  const [hov, setHov] = useState<{ g: GNode; x: number; y: number } | null>(null) // peek card
  const [rf, setRf] = useState<ReactFlowInstance<Node<SporeData>, Edge> | null>(null)

  const load = useCallback(async () => {
    setErr('')
    try {
      const [g, p] = await Promise.all([getGraph(stale), getPins()])
      setPins(p)
      setGraph(g)
    } catch (e) {
      setErr(String(e))
    }
  }, [stale])

  useEffect(() => {
    load()
  }, [load])

  // after a reload, point the selection at the fresh copy of the same thing (or drop it if its gone)
  useEffect(() => {
    if (!graph) return
    setSel((s) => (s && s.id >= 0 ? graph.nodes.find((n) => n.id === s.id) || null : s))
  }, [graph])

  // watch the schedule. when a scan finishes (newest last_run moves), reload the map
  const lastRun = useRef('')
  useEffect(() => {
    let gone = false
    const poll = async () => {
      const s = await getSchedule().catch(() => null)
      if (gone || !s) return
      setSched(s)
      const newest = s.sources.map((x) => x.last_run || '').sort().pop() || ''
      if (lastRun.current && newest !== lastRun.current && !s.running.length) load()
      if (!s.running.length) lastRun.current = newest
    }
    poll()
    const t = window.setInterval(poll, 15000)
    return () => {
      gone = true
      window.clearInterval(t)
    }
  }, [load])

  const scanNow = async () => {
    setScanning(true)
    setErr('')
    try {
      const res = await scanAll()
      const bad = res.filter((r) => r.error)
      if (bad.length) setErr('scan problems: ' + bad.map((r) => r.name + ': ' + r.error).join('; '))
    } catch (e) {
      setErr(String(e))
    }
    setScanning(false)
    await load()
    const s = await getSchedule().catch(() => null)
    if (s) {
      setSched(s)
      lastRun.current = s.sources.map((x) => x.last_run || '').sort().pop() || ''
    }
  }

  const toggleKind = (k: string) => {
    setHide((h) => {
      const n = new Set(h)
      if (n.has(k)) n.delete(k)
      else n.add(k)
      saveHide(n)
      return n
    })
  }

  const view = useMemo(() => (graph ? buildView(graph, infra, hide) : null), [graph, infra, hide])

  // keep the selection ring through a re-layout (pinning something re-lays the map)
  const selId = useRef<number | null>(null)
  selId.current = sel ? sel.id : null

  useEffect(() => {
    if (!view) return
    let gone = false
    place(view, pins)
      .then((ns) => {
        if (!gone) setNodes(ns.map((n) => (selId.current !== null && n.id === String(selId.current) ? { ...n, selected: true } : n)))
      })
      .catch((e) => setErr('layout failed: ' + String(e)))
    return () => {
      gone = true // a newer layout is on its way, drop this one
    }
  }, [view, pins])

  // dropped somewhere = pinned there. a multi-select drag pins all of them
  const pinDrop = (moved: Node<SporeData>[]) => {
    if (!view) return
    const next = new Map(pins)
    for (const n of moved) {
      const id = Number(n.id)
      if (id < 0) continue
      const p = { x: Math.round(n.position.x), y: Math.round(n.position.y), in: boxRef(id, view) }
      next.set(id, p)
      setPin(id, p).catch((e) => setErr(String(e)))
    }
    setPins(next)
  }

  const unpin = (id: number | null) => {
    dropPin(id)
      .then(() => {
        if (id === null) setPins(new Map())
        else {
          const next = new Map(pins)
          next.delete(id)
          setPins(next)
        }
      })
      .catch((e) => setErr(String(e)))
  }

  const ix = useMemo(() => (graph ? index(graph) : null), [graph])
  // the spotlight: everything on the selected thing's traffic paths
  const lit = useMemo(() => (sel && ix ? trace(sel.id, ix) : null), [sel, ix])
  const spot = lit !== null && lit.nodes.size > 1

  // lit cards + the boxes around them stay bright, the rest fades
  const shown = useMemo(() => {
    if (!spot || !view) return nodes
    const keep = new Set<number>()
    for (const id of lit.nodes) {
      for (let p: number | undefined = id; p !== undefined; p = view.parent.get(p)) keep.add(p)
    }
    return nodes.map((n) => {
      const id = Number(n.id)
      const cls = lit.nodes.has(id) ? 'lit' : keep.has(id) || id < 0 ? '' : 'dim'
      return cls ? { ...n, className: cls } : n
    })
  }, [nodes, lit, spot, view])

  // labels only on the line under the mouse, or the lit ones
  const edges: Edge[] = useMemo(() => {
    if (!view) return []
    return view.edges.map((e) => {
      const id = 'e' + e.id
      const on = spot ? lit.edges.has(e.id) : sel !== null && (e.src === sel.id || e.dst === sel.id)
      return {
        id,
        source: String(e.src),
        target: String(e.dst),
        label: hover === id || on ? e.kind : undefined,
        className: 'edge-' + e.kind + (on ? ' near' : spot ? ' dim' : ''),
        // an arrow says which way traffic goes, no marching dashes
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: on ? INK.near : INK.line },
        zIndex: 1000, // or lines into a CT get drawn under its box
      }
    })
  }, [view, hover, sel, lit, spot])

  const drawn = useMemo(() => new Set(nodes.map((n) => Number(n.id))), [nodes])

  // select something and fly the map to it (from the panel's links)
  const jump = useCallback((id: number) => {
    const g = ix?.byId.get(id) || view?.nodes.find((n) => n.id === id)
    if (!g || !drawn.has(id)) return
    setSel(g)
    setNodes((ns) => ns.map((n) => (n.selected === (n.id === String(id)) ? n : { ...n, selected: n.id === String(id) })))
    rf?.fitView({ nodes: [{ id: String(id) }], duration: 450, maxZoom: 1.1, padding: 0.6 })
  }, [ix, view, drawn, rf])

  const clear = useCallback(() => {
    setSel(null)
    setNodes((ns) => ns.map((n) => (n.selected ? { ...n, selected: false } : n)))
  }, [])

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') clear()
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [clear])

  // hover peek card, after a short rest so sweeping the mouse across the map stays calm
  const timer = useRef(0)
  const peek = (e: React.MouseEvent, n: Node<SporeData>) => {
    window.clearTimeout(timer.current)
    if (n.data.g.kind === 'lane') return
    const x = e.clientX
    const y = e.clientY
    timer.current = window.setTimeout(() => setHov({ g: n.data.g, x, y }), 380)
  }
  const unpeek = () => {
    window.clearTimeout(timer.current)
    setHov(null)
  }

  // from the whole graph, not the drawn cards, so a hidden kind keeps its chip to turn it back on
  const kinds = useMemo(() => {
    if (!graph) return []
    const ks = new Set(graph.nodes.map((n) => n.kind))
    if (!infra) ['network', 'storage'].forEach((k) => ks.delete(k)) // those are lanes + chips then
    return ORDER.filter((k) => ks.has(k)).concat(Array.from(ks).filter((k) => !ORDER.includes(k)).sort())
  }, [graph, infra])

  const searchable = useMemo(() => (view ? view.nodes.filter((n) => n.id >= 0 && drawn.has(n.id)) : []), [view, drawn])

  return (
    <div className="app">
      <Toolbar
        nodes={searchable}
        kinds={kinds}
        hide={hide}
        onToggleKind={toggleKind}
        onPick={jump}
        sched={sched}
        scanning={scanning}
        onScan={scanNow}
        infra={infra}
        setInfra={setInfra}
        stale={stale}
        setStale={setStale}
        onReload={load}
        pinCount={pins.size}
        onUnpinAll={() => unpin(null)}
        counts={(graph ? graph.nodes.length : 0) + ' things, ' + edges.length + ' links shown'}
      />
      {err && <div className="err">{err}</div>}
      <main>
        <ReactFlow
          nodes={shown}
          edges={edges}
          nodeTypes={nodeTypes}
          onInit={setRf}
          onNodesChange={(ch) => setNodes((ns) => applyNodeChanges(ch, ns))}
          onNodeClick={(_, n) => {
            unpeek()
            setSel((n as Node<SporeData>).data.g)
          }}
          onPaneClick={clear}
          onNodeMouseEnter={(e, n) => peek(e, n as Node<SporeData>)}
          onNodeMouseLeave={unpeek}
          onNodeDragStart={unpeek}
          onNodeDragStop={(_, __, moved) => pinDrop(moved as Node<SporeData>[])}
          onMoveStart={unpeek}
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
        {sel && ix && view && (
          <Inspector g={sel} ix={ix} view={view} lit={lit} canJump={(id) => drawn.has(id)} onJump={jump} onClose={clear}
            pinned={pins.has(sel.id)} onUnpin={() => unpin(sel.id)} />
        )}
        {hov && ix && <HoverCard g={hov.g} x={hov.x} y={hov.y} ix={ix} />}
      </main>
    </div>
  )
}
