import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import type { GNode } from './api'
import { isOff, kindLabel, type Chip } from './view'

export type SporeData = { g: GNode; chips: Chip[] }

// grant's five for the things that should pop, earthy everything else.
// keep in sync with the :root tokens in styles.css
export const KIND_COLOR: Record<string, string> = {
  endpoint: '#E4A14E', // sunlit clay, where the internet comes in
  app: '#D8F793', // lime cream
  workload: '#A0CA92', // willow green
  container: '#75B09C', // muted teal
  external: '#A1869E', // dusty mauve, tunnels + things outside the lab
  host: '#7A5C43', // bark
  storage: '#9C8A6E', // dry soil
  network: '#8E8B78', // lichen
}

// non-kind colors the canvas needs in js (react flow draws lines + dots itself)
export const INK = { line: '#A89B86', near: '#C97F2A', dots: '#D6CBB7' }

// see-through tints for bridge lanes: moss, sand, teal, mauve. picked by the bridge's number
// (vmbr0 -> moss, vmbr2 -> teal) so the same bridge always looks the same
const LANE_TINT = ['rgba(111, 125, 91, .16)', 'rgba(201, 170, 120, .18)', 'rgba(117, 176, 156, .17)', 'rgba(161, 134, 158, .15)']

function tint(name: string): string {
  const d = name.match(/\d+/)
  let h = d ? Number(d[0]) : 0
  if (!d) for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return LANE_TINT[h % LANE_TINT.length]
}

function cls(base: string, g: GNode, selected: boolean): string {
  return base + (g.stale ? ' stale' : '') + (selected ? ' sel' : '') + (g.confidence < 1 ? ' guess' : '') + (isOff(g) ? ' off' : '')
}

function Chips({ chips }: { chips: Chip[] }) {
  if (!chips.length) return null
  return (
    <div className="chips">
      {chips.map((c, i) => (
        <span key={i} className={'chip chip-' + c.kind} title={c.kind + ': ' + c.name}>{c.name}</span>
      ))}
    </div>
  )
}

function Head({ g, chips }: SporeData) {
  return (
    <>
      <div className="kind">{kindLabel(g) + (isOff(g) ? ' · ' + String(g.attrs.status ?? g.attrs.state ?? 'off') : '')}</div>
      <div className="name" title={g.name}>{g.name}</div>
      <Chips chips={chips} />
    </>
  )
}

// a single thing on the map
export function SporeNode({ data, selected }: NodeProps<Node<SporeData>>) {
  return (
    <div className={cls('spore', data.g, selected)} style={{ borderLeftColor: KIND_COLOR[data.g.kind] || INK.line }}>
      <Handle type="target" position={Position.Left} />
      <Head {...data} />
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

// a bridge lane: a see-through band behind the CTs on that bridge. a CT on two bridges
// sits in a lane striped with both tints
export function LaneNode({ data }: NodeProps<Node<SporeData>>) {
  const nets = (data.g.attrs.nets as { name: string; cidr: string }[]) || []
  const t = nets.map((n) => tint(n.name))
  let bg = t[0] || LANE_TINT[0]
  if (t.length > 1) {
    const stops: string[] = []
    t.forEach((c, i) => stops.push(c + ' ' + i * 14 + 'px ' + (i + 1) * 14 + 'px'))
    bg = 'repeating-linear-gradient(135deg, ' + stops.join(', ') + ')'
  }
  return (
    <div className="lane" style={{ background: bg }}>
      <div className="lane-label">
        {nets.map((n, i) => (
          <span key={n.name}>
            {i > 0 && <i> + </i>}
            <b>{n.name}</b>
            {n.cidr && <span className="cidr">{n.cidr}</span>}
          </span>
        ))}
      </div>
    </div>
  )
}

// a thing with other things inside it (host -> CTs -> containers). react flow sizes it, we just fill it
export function BoxNode({ data, selected }: NodeProps<Node<SporeData>>) {
  return (
    <div className={cls('box box-' + data.g.kind, data.g, selected)} style={{ borderTopColor: KIND_COLOR[data.g.kind] || INK.line }}>
      {/* handles on the header, not halfway down a tall box */}
      <Handle type="target" position={Position.Left} style={{ top: 24 }} />
      <div className="head">
        <Head {...data} />
      </div>
      <Handle type="source" position={Position.Right} style={{ top: 24 }} />
    </div>
  )
}
