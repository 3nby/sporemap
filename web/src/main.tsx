import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import '@xyflow/react/dist/style.css'
import './styles.css'
import { getAuth, onUnauth, type AuthStatus } from './api'
import { App } from './App'
import { Login } from './Login'

// checks the session before anything loads. signed out (with a password set) = login page only
function Gate() {
  const [auth, setAuth] = useState<AuthStatus | null>(null)

  useEffect(() => {
    getAuth().then(setAuth)
    // a 401 later on (session ran out, password changed) drops back to the login page
    onUnauth(() => setAuth((a) => (a ? { ...a, enabled: true, signed_in: false } : a)))
  }, [])

  if (!auth) return null
  if (auth.enabled && !auth.signed_in) return <Login onDone={() => getAuth().then(setAuth)} />
  return <App auth={auth} />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Gate />
  </StrictMode>,
)
