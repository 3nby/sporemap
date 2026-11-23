import { useEffect, useMemo, useRef, useState } from 'react'
import type { GNode, Sched } from './api'
import { ago, str, until } from './fmt'
import { KIND_COLOR } from './SporeNode'
import { kindLabel } from './view'

// --- search -----------------------------------------------------------------

type Hit = { g: GNode; why: string }

// what else besides the name you can find a thing by
function extras(g: GNode): string[] {
  const a = g.attrs
  const out: string[] = []
  if (a.ip) out.push(str(a.ip))
  for (const ip of (a.ips as string[]) || []) out.push(String(ip))
  if (a.image) out.push(str(a.image))
  if (a.content) out.push(str(a.content))
  if (a.vmid) out.push(String(a.vmid))
  return out
}

function Search({ nodes, onPick }: { nodes: GNode[]; onPick: (id: number) => void }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState(0)
  const box = useRef<HTMLInputElement>(null)

  // "/" jumps into the search from anywhere, like most sites do
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (e.key === '/' && t.tagName !== 'INPUT' && t.tagName !== 'TEXTAREA') {
        e.preventDefault()
        box.current?.focus()
      }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])

  const hits: Hit[] = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return []
    const named: Hit[] = []
    const other: Hit[] = []
    for (const g of nodes) {
      if (g.name.toLowerCase().includes(s)) named.push({ g, why: '' })
      else {
        const x = extras(g).find((v) => v.toLowerCase().includes(s))
        if (x) other.push({ g, why: x })
      }
    }
    // names that start with it first, then names that contain it, then ips/images
    named.sort((a, b) => Number(!a.g.name.toLowerCase().startsWith(s)) - Number(!b.g.name.toLowerCase().startsWith(s)))
    return named.concat(other).slice(0, 8)
  }, [q, nodes])

  const pick = (h: Hit | undefined) => {
    if (!h) return
    onPick(h.g.id)
    setQ('')
    setOpen(false)
    box.current?.blur()
  }

  return (
    <div className="search">
      <input
        ref={box}
        value={q}
        placeholder="Search  /"
        onChange={(e) => {
          setQ(e.target.value)
          setAt(0)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)} // let a click on a hit land first
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') setAt((i) => Math.min(i + 1, hits.length - 1))
          else if (e.key === 'ArrowUp') setAt((i) => Math.max(i - 1, 0))
          else if (e.key === 'Enter') pick(hits[at])
          else if (e.key === 'Escape') {
            setQ('')
            box.current?.blur()
          } else return
          e.preventDefault()
        }}
      />
      {open && q.trim() !== '' && (
        <div className="hits">
          {hits.length === 0 && <div className="none">nothing matches</div>}
          {hits.map((h, i) => (
            <button key={h.g.id} className={i === at ? 'on' : ''} onMouseEnter={() => setAt(i)} onMouseDown={() => pick(h)}>
              <i style={{ background: KIND_COLOR[h.g.kind] }} />
              <span className="n">{h.g.name}</span>
              <span className="w">{h.why || kindLabel(h.g)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// --- kind chips (the legend, but you can click it) ------------------------------

function Kinds({ kinds, hide, onToggle }: { kinds: string[]; hide: Set<string>; onToggle: (k: string) => void }) {
  return (
    <div className="kinds">
      {kinds.map((k) => (
        <button key={k} className={'kchip' + (hide.has(k) ? ' off' : '')} onClick={() => onToggle(k)}
          title={(hide.has(k) ? 'show ' : 'hide ') + k}>
          <i style={{ background: KIND_COLOR[k] }} />
          <span>{k}</span>
        </button>
      ))}
    </div>
  )
}

// --- scan status + button --------------------------------------------------------

function Status({ sched, scanning, onScan }: { sched: Sched | null; scanning: boolean; onScan: () => void }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 30000) // keep "x min ago" honest
    return () => window.clearInterval(t)
  }, [])

  const busy = scanning || (sched?.running.length ?? 0) > 0
  let txt = ''
  if (busy) {
    txt = 'scanning ' + (sched?.running.length ? sched.running.join(', ') : 'everything') + '…'
  } else if (sched) {
    const last = sched.sources.map((s) => s.last_run).filter(Boolean).sort().pop()
    const next = sched.sources.map((s) => s.next_run).filter(Boolean).sort()[0]
    txt = (last ? 'updated ' + ago(last) : 'never scanned') + (sched.enabled && next ? ' · next ' + until(next) : '')
  }
  return (
    <div className="status">
      <span className={busy ? 'dot busy' : 'dot'} />
      <span className="txt" title={txt}>{txt}</span>
      <button onClick={onScan} disabled={busy}>{busy ? 'scanning' : 'scan now'}</button>
    </div>
  )
}

// --- the bar --------------------------------------------------------------------

type Props = {
  nodes: GNode[] // searchable (the drawn ones)
  kinds: string[]
  hide: Set<string>
  onToggleKind: (k: string) => void
  onPick: (id: number) => void
  sched: Sched | null
  scanning: boolean
  onScan: () => void
  infra: boolean
  setInfra: (v: boolean) => void
  stale: boolean
  setStale: (v: boolean) => void
  onReload: () => void
  pinCount: number
  onUnpinAll: () => void
  counts: string
}

export function Toolbar(p: Props) {
  const [menu, setMenu] = useState(false)
  return (
    <header>
      <b className="brand">Sporemap</b>
      <Search nodes={p.nodes} onPick={p.onPick} />
      <Kinds kinds={p.kinds} hide={p.hide} onToggle={p.onToggleKind} />
      <Status sched={p.sched} scanning={p.scanning} onScan={p.onScan} />
      <div className="more">
        <button className="dots" onClick={() => setMenu((m) => !m)} title="view options">{'⋯'}</button>
        {menu && (
          <div className="menu" onMouseLeave={() => setMenu(false)}>
            <label><input type="checkbox" checked={p.infra} onChange={(e) => p.setInfra(e.target.checked)} /> bridges + storage as nodes</label>
            <label><input type="checkbox" checked={p.stale} onChange={(e) => p.setStale(e.target.checked)} /> show stale</label>
            <button onClick={() => { p.onReload(); setMenu(false) }}>reload map</button>
            {p.pinCount > 0 && (
              <button onClick={() => {
                if (window.confirm('Unpin all ' + p.pinCount + ' pinned things and go back to the automatic layout?')) p.onUnpinAll()
                setMenu(false)
              }}>{'reset ' + p.pinCount + ' pin' + (p.pinCount > 1 ? 's' : '')}</button>
            )}
            <div className="muted">{p.counts}</div>
          </div>
        )}
      </div>
    </header>
  )
}
