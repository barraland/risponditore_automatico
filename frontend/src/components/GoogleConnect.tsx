import { useEffect, useState } from 'react'
import { useAuth } from '../lib/auth'
import { useTenant } from '../lib/tenant'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

// Card di connessione OAuth Google (Calendar + invio email) del CLIENTE attivo. Sta in una pagina
// sempre visibile (Assistente) e anche in Calendario, così l'email funziona anche se il cliente
// tiene nascosta la voce Calendario.
export default function GoogleConnect() {
  const { session } = useAuth()
  const { aziendaId } = useTenant()
  const tq = aziendaId ? `?azienda_id=${aziendaId}` : ''
  const auth = { Authorization: `Bearer ${session?.access_token}` }
  const [stato, setStato] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  async function caricaStato() {
    if (!API) { setErr('VITE_API_BASE non configurato.'); setLoading(false); return }
    try {
      const res = await fetch(`${API}/google/status${tq}`, { headers: auth })
      const data = await res.json()
      if (!res.ok) setErr(data?.detail || 'Errore'); else setStato(data)
    } catch (e: any) { setErr(e?.message || 'Errore di rete') } finally { setLoading(false) }
  }
  useEffect(() => { caricaStato() }, [aziendaId])

  function connetti() { window.location.href = `${API}/google/connect${tq}` }
  async function disconnetti() {
    if (!confirm('Scollegare Google (Calendar + invio email) per questo cliente?')) return
    await fetch(`${API}/google/disconnect${tq}`, { method: 'POST', headers: auth })
    setStato({ connesso: false })
  }

  const connesso = stato?.connesso
  return (
    <div className="pw-card">
      <div className="pw-card-head"><h3>Google — Calendar & Email</h3></div>
      <div className="pw-card-body">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : err ? <div className="pw-error">{err}</div>
          : connesso ? (
            <div className="pw-row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
              <div>
                <div style={{ fontWeight: 600, color: 'var(--fg)' }}>Connesso ✓</div>
                <div className="pw-muted" style={{ fontSize: 13 }}>{stato.email || '—'} · {stato.calendar_id || 'primary'}</div>
                <div className="pw-muted" style={{ fontSize: 13, marginTop: 2 }}>
                  Invio email: {stato.email_attiva
                    ? <span style={{ color: 'var(--accent, #6366f1)' }}>attivo da questa casella</span>
                    : <span>non concesso — <button className="pw-btn pw-btn-ghost pw-btn-sm" style={{ padding: '0 4px' }} onClick={connetti}>riconnetti</button> per abilitarlo</span>}
                </div>
              </div>
              <button className="pw-btn pw-btn-ghost" onClick={disconnetti}>Disconnetti</button>
            </div>
          ) : (
            <div className="pw-row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
              <div className="pw-muted" style={{ maxWidth: 560 }}>
                Nessun account Google collegato per questo cliente. Collegalo per prenotare meeting sul
                suo calendario e inviare le email <strong>dalla sua casella</strong>. Facoltativo.
              </div>
              <button className="pw-btn pw-btn-primary" onClick={connetti}>Connetti Google</button>
            </div>
          )}
      </div>
    </div>
  )
}
