import { useEffect, useState } from 'react'
import { getNode, type GNode, type NodeDetail } from './api'
import { facts, statusOf, type Idx } from './facts'
import { ago, day } from './fmt'
import { KIND_COLOR } from './SporeNode'
import type { Lit } from './trace'
import { isOff, kindLabel, type View } from './view'

type Props = {
  g: GNode
  ix: Idx
  view: View
  lit: Lit | null
  canJump: (id: number) => boolean
  onJump: (id: number) => void
  onClose: () => void
}

// a name you can click to jump to it on the map (or plain text when its not drawn)
function Ref({ id, ix, canJump, onJump }: { id: number; ix: Idx; canJump: Props['canJump']; onJump: Props['onJump'] }) {
  const n = ix.byId.get(id)
  if (!n) return null
  const label = <><i style={{ background: KIND_COLOR[n.kind] }} />{n.name}</>
  if (!canJump(id)) return <span className="ref off">{label}</span>
  return <button className="ref" onClick={() => onJump(id)} title={kindLabel(n)}>{label}</button>
}

export function Inspector({ g, ix, view, lit, canJump, onJump, onClose }: Props) {
  const [det, setDet] = useState<NodeDetail | null>(null)

  useEffect(() => {
    let gone = false
    setDet(null)
    getNode(g.id).then((d) => {
      if (!gone) setDet(d)
    }).catch(() => {})
    return () => {
      gone = true
    }
  }, [g.id])

  const st = statusOf(g)
  const r = { ix, canJump, onJump }

  // breadcrumb from the drawn view, so bridge lanes show up in it too
  const crumbs: GNode[] = []
  for (let p = view.parent.get(g.id); p !== undefined; p = view.parent.get(p)) {
    const n = view.nodes.find((x) => x.id === p)
    if (n) crumbs.unshift(n)
  }

  const out = ix.out.get(g.id) || []
  const inn = ix.inn.get(g.id) || []
  const group = (es: typeof out, kind: string, end: 'src' | 'dst') => es.filter((e) => e.kind === kind).map((e) => e[end])
  const conns: [string, number[]][] = [
    ['reached from', group(inn, 'routes_to', 'src')],
    ['sends to', group(out, 'routes_to', 'dst')],
    ['contains', ix.kids.get(g.id) || []],
    ['bridges', group(out, 'member_of', 'dst')],
    ['storage', group(out, 'mounts', 'dst')],
  ]

  return (
    <aside className="insp">
      <div className="insp-top">
        <span className="tag" style={{ background: KIND_COLOR[g.kind] }}>{kindLabel(g)}</span>
        {st && <span className={'pill' + (isOff(g) ? ' pill-off' : '')}>{st}</span>}
        <button className="x" onClick={onClose} title="close (Esc)">{'×'}</button>
      </div>
      <h2>{g.name}</h2>
      {crumbs.length > 0 && (
        <div className="crumbs">
          {crumbs.map((c) => (
            <span key={c.id}>
              {c.kind === 'lane' ? <span className="lane-crumb">{c.name}</span> : <button onClick={() => onJump(c.id)}>{c.name}</button>}
              <i>{'›'}</i>
            </span>
          ))}
          <b>{g.name}</b>
        </div>
      )}

      <dl className="facts">
        {facts(g, ix).map((f, i) => (
          <div key={i}>
            <dt>{f.k}</dt>
            <dd>{f.href ? <a href={f.href} target="_blank" rel="noreferrer">{f.v}</a> : f.v}</dd>
          </div>
        ))}
      </dl>

      {lit && lit.paths.length > 0 && (
        <section>
          <h3>traffic path{lit.paths.length > 1 ? 's' : ''}</h3>
          {lit.paths.map((p, i) => (
            <div key={i} className="path">
              {p.map((id, j) => (
                <span key={id}>
                  {j > 0 && <i className="arrow">{'→'}</i>}
                  <Ref id={id} {...r} />
                </span>
              ))}
            </div>
          ))}
        </section>
      )}

      {conns.filter(([, ids]) => ids.length).map(([k, ids]) => (
        <section key={k}>
          <h3>{k + (ids.length > 3 ? ' (' + ids.length + ')' : '')}</h3>
          <div className="refs">{ids.map((id) => <Ref key={id} id={id} {...r} />)}</div>
        </section>
      ))}

      <section className="seen">
        <h3>seen by</h3>
        {det && det.seen_by.length > 0 && (
          <div className="refs">
            {det.seen_by.map((s) => (
              <span key={s.source + s.ext_id} className="src" title={s.ext_id}>
                {s.source}
                {/* docker ids are 64 char hashes, the full one is in the tooltip */}
                <small>{s.ext_id.length > 22 ? s.ext_id.slice(0, 16) + '…' : s.ext_id}</small>
              </span>
            ))}
          </div>
        )}
        <div className="muted">{'first seen ' + day(g.first_seen) + ' · updated ' + ago(g.last_seen)}</div>
      </section>

      <details className="raw">
        <summary>raw data</summary>
        <pre>{JSON.stringify(g.attrs, null, 2)}</pre>
      </details>
    </aside>
  )
}
