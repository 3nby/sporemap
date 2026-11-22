import type { GNode } from './api'
import { facts, statusOf, type Idx } from './facts'
import { KIND_COLOR } from './SporeNode'
import { isOff, kindLabel } from './view'

// the little peek card that shows after resting the mouse on something
export function HoverCard({ g, x, y, ix }: { g: GNode; x: number; y: number; ix: Idx }) {
  const st = statusOf(g)
  const f = facts(g, ix).filter((r) => !r.href).slice(0, 3) // links are for the panel
  // keep it on screen
  const left = Math.min(x + 16, window.innerWidth - 270)
  const top = Math.min(y + 16, window.innerHeight - 150)
  return (
    <div className="hover" style={{ left, top, borderLeftColor: KIND_COLOR[g.kind] }}>
      <div className="kind">{kindLabel(g)}{st && <span className={'pill' + (isOff(g) ? ' pill-off' : '')}>{st}</span>}</div>
      <div className="name">{g.name}</div>
      {f.map((r, i) => (
        <div key={i} className="row"><span>{r.k}</span>{r.v}</div>
      ))}
      <div className="hint">click for details</div>
    </div>
  )
}
