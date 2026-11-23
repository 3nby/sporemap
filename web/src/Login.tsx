import { useState } from 'react'
import { login } from './api'

// the only thing you see until you sign in. no lab data comes through before that
export function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const go = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!pw || busy) return
    setBusy(true)
    setErr('')
    const why = await login(pw).catch((x) => String(x))
    setBusy(false)
    if (why) {
      setErr(why)
      setPw('')
      return
    }
    onDone()
  }

  return (
    <div className="login">
      <form onSubmit={go}>
        <b className="brand">Sporemap</b>
        <label htmlFor="pw">Password</label>
        <input id="pw" type="password" autoFocus autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} />
        {err && <div className="lerr">{err}</div>}
        <button type="submit" disabled={busy || !pw}>{busy ? 'signing in...' : 'Sign in'}</button>
        <div className="muted">Forgot it? On the server: sporemap set-password</div>
      </form>
    </div>
  )
}
