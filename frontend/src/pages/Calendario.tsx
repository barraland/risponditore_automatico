import { useEffect, useState } from 'react'
import { useAuth } from '../lib/auth'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')
const GIORNI = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']

function lunedi(offset: number): Date {
  const d = new Date(); d.setHours(0, 0, 0, 0)
  const day = (d.getDay() + 6) % 7 // 0 = lunedì
  d.setDate(d.getDate() - day + offset * 7)
  return d
}
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x }
const isoDay = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
const fmtOra = (iso: string) => new Date(iso).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
const fmtGiorno = (d: Date) => d.toLocaleDateString('it-IT', { day: '2-digit', month: 'short' })

export default function Calendario() {
  const { session } = useAuth()
  const [stato, setStato] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [week, setWeek] = useState(0)
  const [eventi, setEventi] = useState<any[]>([])
  const [loadingEv, setLoadingEv] = useState(false)

  const auth = { Authorization: `Bearer ${session?.access_token}` }

  async function caricaStato() {
    if (!API) { setErr('VITE_API_BASE non configurato.'); setLoading(false); return }
    try {
      const res = await fetch(`${API}/google/status`, { headers: auth })
      const data = await res.json()
      if (!res.ok) setErr(data?.detail || 'Errore'); else setStato(data)
    } catch (e: any) { setErr(e?.message || 'Errore di rete') } finally { setLoading(false) }
  }
  useEffect(() => { caricaStato() }, [])

  async function caricaEventi() {
    const da = lunedi(week), a = addDays(da, 7)
    setLoadingEv(true)
    try {
      const res = await fetch(`${API}/google/events?da=${encodeURIComponent(da.toISOString())}&a=${encodeURIComponent(a.toISOString())}`, { headers: auth })
      const data = await res.json()
      setEventi(res.ok ? (data.eventi || []) : [])
    } catch { setEventi([]) } finally { setLoadingEv(false) }
  }
  useEffect(() => { if (stato?.connesso) caricaEventi() }, [stato?.connesso, week])

  function connetti() { window.location.href = `${API}/google/connect` }
  async function disconnetti() {
    if (!confirm('Scollegare Google Calendar?')) return
    await fetch(`${API}/google/disconnect`, { method: 'POST', headers: auth })
    setStato({ connesso: false }); setEventi([])
  }

  const connesso = stato?.connesso
  const giorni = Array.from({ length: 7 }, (_, i) => addDays(lunedi(week), i))
  const oggiKey = isoDay(new Date())
  const range = `${fmtGiorno(giorni[0])} – ${fmtGiorno(giorni[6])}`

  return (
    <div className="pw-stack" style={{ maxWidth: 1000 }}>
      <div>
        <div className="pw-eyebrow">Integrazioni</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Google Calendar</h1>
        <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>
          Calendario collegato in sola lettura. Presto l'assistente potrà prenotare qui i meeting.
        </div>
      </div>

      <div className="pw-card">
        <div className="pw-card-body">
          {loading ? <div className="pw-spinner">Caricamento…</div>
            : err ? <div className="pw-error">{err}</div>
            : connesso ? (
              <div className="pw-row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
                <div><div style={{ fontWeight: 600, color: 'var(--fg)' }}>Connesso ✓</div>
                  <div className="pw-muted" style={{ fontSize: 13 }}>{stato.email || '—'} · {stato.calendar_id || 'primary'}</div></div>
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

      {connesso && (
        <div className="pw-card">
          <div className="pw-card-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ textTransform: 'capitalize' }}>{range}</h3>
            <div className="pw-row" style={{ gap: 6 }}>
              <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setWeek(week - 1)}>‹</button>
              <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setWeek(0)}>Oggi</button>
              <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setWeek(week + 1)}>›</button>
            </div>
          </div>
          <div className="pw-card-body">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 6, minHeight: 260 }}>
              {giorni.map((d, i) => {
                const eventiGiorno = eventi
                  .filter(e => isoDay(new Date(e.inizio)) === isoDay(d))
                  .sort((a, b) => (a.inizio < b.inizio ? -1 : 1))
                const oggi = isoDay(d) === oggiKey
                return (
                  <div key={i} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 6,
                    background: oggi ? 'var(--bg-2, #f5f7fb)' : 'transparent', minHeight: 240 }}>
                    <div style={{ textAlign: 'center', marginBottom: 6, fontSize: 12,
                      fontWeight: oggi ? 700 : 500, color: oggi ? 'var(--accent, #2563eb)' : 'var(--fg-2)' }}>
                      {GIORNI[i]}<br /><span style={{ fontSize: 15 }}>{d.getDate()}</span>
                    </div>
                    <div className="pw-stack" style={{ gap: 4 }}>
                      {loadingEv ? <div className="pw-muted" style={{ fontSize: 11 }}>…</div>
                        : eventiGiorno.length === 0 ? null
                        : eventiGiorno.map(e => (
                          <div key={e.id} title={`${e.titolo}${e.dove ? ' · ' + e.dove : ''}`}
                            style={{ background: 'var(--accent-soft, #e6efff)', color: 'var(--accent, #1e40af)',
                              borderRadius: 6, padding: '4px 6px', fontSize: 11, lineHeight: 1.25, overflow: 'hidden' }}>
                            <div style={{ fontWeight: 600 }}>{e.allday ? 'Tutto il giorno' : fmtOra(e.inizio)}</div>
                            <div style={{ whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden' }}>{e.titolo}</div>
                          </div>
                        ))}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
