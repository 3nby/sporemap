import { useEffect, useState } from 'react'
import { getOverview, type Overview as Ov } from './api'
import { ago, bytes } from './fmt'
import { KIND_COLOR } from './SporeNode'

// the landing page: how the lab is doing, what needs a look, how full the hosts are.
// status colors (good / warn / bad) only ever show up next to an icon and a word

type Level = 'good' | 'warn' | 'bad' | 'calm'
const ICON: Record<string, string> = { good: '✓', warn: '!', bad: '✕', calm: '•', info: 'i' }

// a health tile: big count, total, and what the gap means
function Tile({ label, good, total, gap, level }: { label: string; good: number; total: number; gap: string; level: Level }) {
  const ok = good === total
  return (
    <div className="tile">
      <div className="tlabel">{label}</div>
      <div className="tvalue">{good}<span>{' of ' + total}</span></div>
      <div className={'tnote ' + (ok ? 'good' : level)}>
        <i>{ICON[ok ? 'good' : level]}</i>
        {ok ? (total ? 'all good' : 'none yet') : gap}
      </div>
    </div>
  )
}

// one meter row: name, bar, figure. the bar caps at full, the figure says how far over
function Meter({ name, used, cap, fmt, unit = '', warnAt, badAt, title }: {
  name: string; used: number; cap: number | null; fmt: (n: number) => string; unit?: string; warnAt: number; badAt: number; title: string
}) {
  if (!cap) return null
  const pct = Math.round((used / cap) * 100)
  const lvl = pct > badAt ? 'bad' : pct > warnAt ? 'warn' : 'good'
  return (
    <div className="meter" title={title}>
      <div className="mname">{name}</div>
      <div className={'mbar ' + lvl}><div style={{ width: Math.min(pct, 100) + '%' }} /></div>
      <div className="mfig">
        {fmt(used) + ' of ' + fmt(cap) + unit}
        <b className={lvl}>{' ' + pct + '%'}</b>
        {pct > 100 && <span className="over">overcommitted</span>}
      </div>
    </div>
  )
}

export function Overview({ tick, onShow }: { tick: unknown; onShow: (id: number) => void }) {
  const [ov, setOv] = useState<Ov | null>(null)
  const [err, setErr] = useState('')

  // reload whenever the map's data reloads (scan finished, scan now, reload)
  useEffect(() => {
    let gone = false
    getOverview().then((o) => {
      if (!gone) setOv(o)
    }).catch((e) => setErr(String(e)))
    return () => {
      gone = true
    }
  }, [tick])

  if (err) return <div className="ov"><div className="err">{err}</div></div>
  if (!ov) return <div className="ov"><div className="muted">loading...</div></div>

  const att = ov.attention
  const has = (what: string, level: string) => att.some((a) => a.what === what && a.level === level)
  const h = ov.health
  const total = Object.values(ov.counts).reduce((a, b) => a + b, 0)

  return (
    <div className="ov">
      <section className="tiles">
        <Tile label="CTs and VMs running" good={h.workloads[0]} total={h.workloads[1]}
          gap={h.workloads[1] - h.workloads[0] + ' stopped'} level="calm" />
        <Tile label="Containers healthy" good={h.containers[0]} total={h.containers[1]}
          gap={h.containers[1] - h.containers[0] + ' unhealthy or down'} level="bad" />
        <Tile label="Tunnels up" good={h.tunnels[0]} total={h.tunnels[1]}
          gap={h.tunnels[1] - h.tunnels[0] + (has('tunnel', 'bad') ? ' down' : ' not in use')} level={has('tunnel', 'bad') ? 'bad' : 'calm'} />
        <Tile label="Sources scanning fine" good={h.sources[0]} total={h.sources[1]}
          gap={h.sources[1] - h.sources[0] + ' failing'} level="bad" />
      </section>

      <div className="ov-cols">
        <section className="card attention">
          <h3>Needs a look</h3>
          {att.length === 0 && <div className="allgood"><i>{ICON.good}</i>Nothing needs a look.</div>}
          {att.map((a, i) => (
            <div key={i} className={'att ' + a.level}>
              <i>{ICON[a.level === 'info' ? 'info' : a.level]}</i>
              <span className="atext">{a.text}</span>
              {a.node_id !== null && <button onClick={() => onShow(a.node_id as number)}>show on map</button>}
            </div>
          ))}
        </section>

        <div className="ov-side">
          {ov.capacity.map((c) => (
            <section key={c.id} className="card">
              <h3>
                <button className="hostlink" onClick={() => onShow(c.id)}>{c.name}</button>
                <span className="muted">{c.running + ' of ' + c.total + ' CTs/VMs running'}</span>
              </h3>
              {/* cpu overcommit is normal on lxc, memory overcommit is the one to watch */}
              <Meter name="CPU" used={c.cpus_running} cap={c.cpus} fmt={String} unit=" cores" warnAt={200} badAt={400}
                title={'running CTs/VMs ask for ' + c.cpus_running + ' cores, ' + c.cpus_all + ' counting stopped ones'} />
              <Meter name="Memory" used={c.mem_running} cap={c.mem} fmt={bytes} warnAt={85} badAt={125}
                title={'running CTs/VMs are allowed ' + bytes(c.mem_running) + ', ' + bytes(c.mem_all) + ' counting stopped ones'} />
            </section>
          ))}

          <section className="card">
            <h3>Sources</h3>
            <table className="srcs">
              <tbody>
                {ov.sources.map((s) => (
                  <tr key={s.name}>
                    <td className={'st ' + (s.status === 'ok' ? 'good' : s.status === 'failed' ? 'bad' : 'calm')}>
                      <i>{ICON[s.status === 'ok' ? 'good' : s.status === 'failed' ? 'bad' : 'calm']}</i>{s.status}
                    </td>
                    <td className="sname">{s.name}</td>
                    <td className="muted">{ago(s.at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {ov.sources.filter((s) => s.error).map((s) => (
              <div key={s.name} className="srcerr">{s.name + ': ' + s.error}</div>
            ))}
            {ov.sources.length === 0 && <div className="muted">No scans yet. Add sources to sporemap.yaml, then scan now.</div>}
          </section>

          <section className="card">
            <h3>{total + ' things mapped'}</h3>
            <div className="counts">
              {Object.entries(ov.counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
                <span key={k}><i style={{ background: KIND_COLOR[k] }} />{n + ' ' + k}</span>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
