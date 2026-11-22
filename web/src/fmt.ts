// small formatters for the inspector + hover card

export function bytes(n: unknown): string {
  let v = Number(n)
  if (!isFinite(v) || v <= 0) return ''
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let i = 0
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return (v >= 10 || i === 0 ? Math.round(v) : Math.round(v * 10) / 10) + ' ' + u[i]
}

export function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (!isFinite(s)) return ''
  if (s < 60) return 'just now'
  if (s < 3600) return Math.round(s / 60) + ' min ago'
  if (s < 86400) return Math.round(s / 3600) + ' h ago'
  return Math.round(s / 86400) + ' days ago'
}

export function day(iso: string): string {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function str(v: unknown): string {
  return v === undefined || v === null ? '' : String(v)
}
