import ELK, { type ElkNode } from 'elkjs/lib/elk-api.js'
// the layout engine itself is ~1.5mb. as a worker it loads on its own, off the main bundle,
// and big layouts dont freeze the page while they run
import workerUrl from 'elkjs/lib/elk-worker.min.js?url'

const elk = new ELK({ workerUrl })

export const W = 190
export const H = 54
export const H_CHIPS = 76 // a card with a row of chips under the name
export const TOP = 46 // header strip of a box that holds other things
export const TOP_CHIPS = 68
export const TOP_LANE = 30 // bridge lanes just have a one-line label

// a card, or a box with cards (and boxes) inside it
export type Box = { id: string; w: number; h: number; top: number; kids: Box[] }
// where it ended up. x/y are relative to the parent box, which is what react flow wants too
export type Placed = { id: string; parent?: string; x: number; y: number; w: number; h: number }
export type Link = { id: string; source: string; target: string }

// left to right: internet stuff on the left, boxes and containers on the right
const opts = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.hierarchyHandling': 'INCLUDE_CHILDREN', // lay out lines that cross into boxes too
  'elk.layered.spacing.nodeNodeBetweenLayers': '80',
  'elk.spacing.nodeNode': '24',
  'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
  'elk.separateConnectedComponents': 'false', // loose pairs join the columns instead of sinking to the bottom
}

function toElk(b: Box, inner: Set<string>): ElkNode {
  if (!b.kids.length) return { id: b.id, width: b.w, height: b.h }
  const lo: Record<string, string> = {
    'elk.padding': '[top=' + b.top + ',left=14,bottom=14,right=14]',
    'elk.nodeSize.constraints': 'MINIMUM_SIZE', // so the header never gets squished
    'elk.nodeSize.minimum': '(' + (W + 28) + ',' + (b.top + 20) + ')',
  }
  // a CT full of containers with no lines between them would be one long column,
  // so pack those into a grid instead (lines from outside still find them)
  const flat = b.kids.length > 3 && b.kids.every((k) => !k.kids.length && !inner.has(k.id))
  if (flat) {
    lo['elk.hierarchyHandling'] = 'SEPARATE_CHILDREN'
    lo['elk.algorithm'] = 'rectpacking'
    lo['elk.aspectRatio'] = '1.6'
    lo['elk.spacing.nodeNode'] = '12'
  }
  return { id: b.id, children: b.kids.map((k) => toElk(k, inner)), layoutOptions: lo }
}

// ids of cards that have a line to a sibling inside the same box
function siblingLinked(roots: Box[], links: Link[]): Set<string> {
  const par = new Map<string, string>()
  const walkB = (b: Box) => b.kids.forEach((k) => { par.set(k.id, b.id); walkB(k) })
  roots.forEach(walkB)
  const out = new Set<string>()
  for (const e of links) {
    if (par.get(e.source) !== undefined && par.get(e.source) === par.get(e.target)) {
      out.add(e.source)
      out.add(e.target)
    }
  }
  return out
}

function walk(n: ElkNode, parent: string | undefined, out: Placed[]) {
  for (const c of n.children || []) {
    out.push({ id: c.id, parent, x: c.x || 0, y: c.y || 0, w: c.width || 0, h: c.height || 0 })
    walk(c, c.id, out) // parents land before their kids, react flow needs that order
  }
}

export async function layout(roots: Box[], links: Link[]): Promise<Placed[]> {
  const inner = siblingLinked(roots, links)
  const g = await elk.layout({
    id: 'root',
    layoutOptions: opts,
    children: roots.map((r) => toElk(r, inner)),
    edges: links.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  })
  const out: Placed[] = []
  walk(g, undefined, out)
  _liftColumns(out, new Set(roots.filter((r) => !r.kids.length).map((r) => r.id)))
  // console.log('placed', out.length)
  return out
}

// elk lines each domain up with whatever it points at, which sits halfway down the big
// host box, so the domain column starts way below the top. slide each loose column up
// to the top edge and keep its order + spacing
function _liftColumns(out: Placed[], loose: Set<string>) {
  const top = Math.min(...out.filter((p) => !p.parent).map((p) => p.y))
  const cols = new Map<number, Placed[]>()
  for (const p of out) {
    if (p.parent || !loose.has(p.id)) continue
    const x = Math.round(p.x)
    cols.set(x, (cols.get(x) || []).concat(p))
  }
  const boxes = out.filter((p) => !p.parent && !loose.has(p.id))
  for (const c of cols.values()) {
    const x0 = c[0].x
    const x1 = x0 + Math.max(...c.map((p) => p.w))
    if (boxes.some((b) => b.x < x1 && b.x + b.w > x0)) continue // a box shares this strip, sliding up would land on it
    c.sort((a, b) => a.y - b.y)
    let y = top
    for (const p of c) {
      p.y = y
      y += p.h + 18
    }
  }
}
