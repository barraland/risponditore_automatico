import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth'

export default function Login() {
  const { session, signIn } = useAuth()
  const nav = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (session) return <Navigate to="/societa" replace />

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setErr(null)
    const { error } = await signIn(email.trim(), password)
    setBusy(false)
    if (error) setErr(error)
    else nav('/societa')
  }

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 20 }}>
      <div className="pw-card" style={{ width: 380, maxWidth: '100%' }}>
        <div className="pw-card-body" style={{ display: 'grid', gap: 18 }}>
          <div className="pw-row" style={{ gap: 10 }}>
            <img src="/pipework-mark.svg" alt="" width={30} height={30} />
            <div>
              <div className="pw-eyebrow">Pipework · HORECA</div>
              <h2 style={{ fontSize: 20, marginTop: 4 }}>Accedi</h2>
            </div>
          </div>
          <form onSubmit={submit} style={{ display: 'grid', gap: 14 }}>
            <div className="pw-field">
              <label>Email</label>
              <input className="pw-input" type="email" autoComplete="email" required
                     value={email} onChange={e => setEmail(e.target.value)} />
            </div>
            <div className="pw-field">
              <label>Password</label>
              <input className="pw-input" type="password" autoComplete="current-password" required
                     value={password} onChange={e => setPassword(e.target.value)} />
            </div>
            {err && <div className="pw-error">{err}</div>}
            <button className="pw-btn pw-btn-primary" disabled={busy} style={{ justifyContent: 'center' }}>
              {busy ? 'Accesso…' : 'Entra'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
