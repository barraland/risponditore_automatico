import { useEffect, useState } from 'react'
import { useAuth } from '../lib/auth'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

export default function Calendario() {
  const { session } = useAuth()
  const [stato, setStato] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  async function carica() {
    if (!API) { setErr('VITE_API_BASE non configurato.'); setLoading(false); return }
    try {
      const res = await fetch(`${API}/google/status`, { headers: { Authorization: `Bearer ${session?.access_token}` } })
      const data = await res.json()
      if (!res.ok) setErr(data?.detail || 'Errore'); else setStato(data)
    } catch (e: any) { setErr(e?.message || 'Errore di rete') } finally { setLoading(false) }
  }
  useEffect(() => { carica() }, [])

  function connetti() {
    // Redirect a tutta pagina verso il backend → schermata di consenso Google → callback → torna qui.
    window.location.href = `${API}/google/connect`
  }
  async function disconnetti() {
    if (!confirm('Scollegare Google Calendar?')) return
    await fetch(`${API}/google/disconnect`, { method: 'POST', headers: { Authorization: `Bearer ${session?.access_token}` } })
    carica()
  }

  const connesso = stato?.connesso
  const appenaConnesso = new URLSearchParams(location.search).get('connected') === '1'

  return (
    <div className="pw-stack" style={{ maxWidth: 720 }}>
      <div>
        <div className="pw-eyebrow">Integrazioni</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Google Calendar</h1>
        <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>
          Collega il tuo Google Calendar: l'assistente potrà prenotare i meeting con i clienti che
          chiamano. La connessione avviene con il tuo account Google (OAuth), i dati restano sul tuo
          calendario.
        </div>
      </div>

      {appenaConnesso && !err && (
        <div className="pw-card" style={{ borderColor: 'var(--ok, #2e7d32)' }}>
          <div className="pw-card-body">✅ Connesso a Google Calendar.</div>
        </div>
      )}

      <div className="pw-card">
        <div className="pw-card-body">
          {loading ? <div className="pw-spinner">Caricamento…</div>
            : err ? <div className="pw-error">{err}</div>
            : connesso ? (
              <div className="pw-row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
                <div>
                  <div style={{ fontWeight: 600, color: 'var(--fg)' }}>Connesso ✓</div>
                  <div className="pw-muted" style={{ fontSize: 13 }}>
                    Account: {stato.email || '—'} · Calendario: {stato.calendar_id || 'primary'}
                  </div>
                </div>
                <button className="pw-btn pw-btn-ghost" onClick={disconnetti}>Disconnetti</button>
              </div>
            ) : (
              <div className="pw-row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
                <div className="pw-muted">Nessun calendario collegato.</div>
                <button className="pw-btn pw-btn-primary" onClick={connetti}>Connetti a Google Calendar</button>
              </div>
            )}
        </div>
      </div>

      <div className="pw-muted" style={{ fontSize: 12 }}>
        La prenotazione dei meeting da parte dell'assistente arriva nello step successivo, una volta
        collegato il calendario.
      </div>
    </div>
  )
}
