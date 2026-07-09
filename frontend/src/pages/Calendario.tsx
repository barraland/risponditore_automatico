import { useEffect, useState } from 'react'
import { useAuth } from '../lib/auth'
import { useTenant } from '../lib/tenant'
import GoogleConnect from '../components/GoogleConnect'

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

const HOUR_PX = 54          // altezza di un'ora (px): più alto = più leggibile
const AX = 52               // larghezza colonna degli orari
const hourOf = (iso: string) => { const d = new Date(iso); return d.getHours() + d.getMinutes() / 60 }

// Assegna a ciascun evento (con orario) una "corsia" per affiancare i sovrapposti (stile Google).
function layoutGiorno(evs: any[]) {
  const items = evs.map(e => {
    const s = hourOf(e.inizio)
    const en = e.fine ? Math.max(hourOf(e.fine), s + 0.25) : s + 1
    return { e, startH: s, endH: en, lane: 0, lanes: 1 }
  }).sort((a, b) => a.startH - b.startH || a.endH - b.endH)

  let cluster: typeof items = []
  let clusterEnd = -Infinity
  const flush = () => {
    const laneEnds: number[] = []
    cluster.forEach(it => {
      let lane = laneEnds.findIndex(end => end <= it.startH + 1e-6)
      if (lane === -1) { lane = laneEnds.length; laneEnds.push(it.endH) } else laneEnds[lane] = it.endH
      it.lane = lane
    })
    cluster.forEach(it => (it.lanes = laneEnds.length))
    cluster = []; clusterEnd = -Infinity
  }
  items.forEach(it => {
    if (cluster.length && it.startH >= clusterEnd - 1e-6) flush()
    cluster.push(it); clusterEnd = Math.max(clusterEnd, it.endH)
  })
  flush()
  return items
}

function GrigliaSettimana({ giorni, eventi, oggiKey, loadingEv }: {
  giorni: Date[]; eventi: any[]; oggiKey: string; loadingEv: boolean
}) {
  const timed = eventi.filter(e => !e.allday && e.inizio)
  let minH = 8, maxH = 20
  timed.forEach(e => {
    const s = hourOf(e.inizio), en = e.fine ? hourOf(e.fine) : s + 1
    minH = Math.min(minH, Math.floor(s)); maxH = Math.max(maxH, Math.ceil(en))
  })
  minH = Math.max(0, Math.min(minH, 23)); maxH = Math.min(24, Math.max(maxH, minH + 1))
  const ore = Array.from({ length: maxH - minH + 1 }, (_, i) => minH + i)
  const totalH = (maxH - minH) * HOUR_PX
  const alldayWeek = eventi.filter(e => e.allday)

  return (
    <div style={{ overflowX: 'auto' }}>
      <div style={{ minWidth: 720 }}>
        {/* intestazione giorni */}
        <div style={{ display: 'flex' }}>
          <div style={{ width: AX, flexShrink: 0 }} />
          {giorni.map((d, i) => {
            const oggi = isoDay(d) === oggiKey
            return (
              <div key={i} style={{ flex: 1, textAlign: 'center', padding: '2px 0 6px', fontSize: 12,
                fontWeight: oggi ? 700 : 500, color: oggi ? 'var(--accent, #2563eb)' : 'var(--fg-2)' }}>
                {GIORNI[i]} <span style={{ fontSize: 15 }}>{d.getDate()}</span>
              </div>
            )
          })}
        </div>

        {/* riga eventi "tutto il giorno" */}
        {alldayWeek.length > 0 && (
          <div style={{ display: 'flex', borderTop: '1px solid var(--border)' }}>
            <div style={{ width: AX, flexShrink: 0, fontSize: 10, color: 'var(--fg-2)',
              display: 'flex', alignItems: 'center', justifyContent: 'center' }}>tutto il g.</div>
            {giorni.map((d, i) => (
              <div key={i} style={{ flex: 1, borderLeft: '1px solid var(--border)', padding: 2, minHeight: 22,
                display: 'flex', flexDirection: 'column', gap: 2 }}>
                {alldayWeek.filter(e => isoDay(new Date(e.inizio)) === isoDay(d)).map(e => (
                  <div key={e.id} title={e.titolo} style={{ background: 'var(--accent-soft, #e6efff)',
                    color: 'var(--accent, #1e40af)', borderRadius: 4, padding: '1px 5px', fontSize: 11,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.titolo}</div>
                ))}
              </div>
            ))}
          </div>
        )}

        {/* griglia oraria */}
        <div style={{ display: 'flex', borderTop: '1px solid var(--border)' }}>
          {/* asse degli orari */}
          <div style={{ width: AX, flexShrink: 0, position: 'relative', height: totalH }}>
            {ore.map(h => (
              <div key={h} style={{ position: 'absolute', top: (h - minH) * HOUR_PX - 6, right: 6,
                fontSize: 10, color: 'var(--fg-2)' }}>{String(h).padStart(2, '0')}:00</div>
            ))}
          </div>
          {/* colonne dei giorni */}
          {giorni.map((d, i) => {
            const oggi = isoDay(d) === oggiKey
            const items = layoutGiorno(timed.filter(e => isoDay(new Date(e.inizio)) === isoDay(d)))
            return (
              <div key={i} style={{ flex: 1, position: 'relative', height: totalH,
                borderLeft: '1px solid var(--border)', background: oggi ? 'var(--bg-2, #f7f9fc)' : 'transparent' }}>
                {/* fasce orarie: linea tratteggiata a ogni ora + mezz'ora più tenue */}
                {ore.map(h => (
                  <div key={h}>
                    <div style={{ position: 'absolute', left: 0, right: 0, top: (h - minH) * HOUR_PX,
                      borderTop: '1px dashed var(--border)' }} />
                    {h < maxH && <div style={{ position: 'absolute', left: 0, right: 0,
                      top: (h - minH) * HOUR_PX + HOUR_PX / 2, borderTop: '1px dashed var(--border)', opacity: 0.4 }} />}
                  </div>
                ))}
                {/* eventi posizionati per orario */}
                {items.map(({ e, startH, endH, lane, lanes }) => {
                  const top = Math.max(0, startH - minH) * HOUR_PX
                  const bottom = Math.min(totalH, (endH - minH) * HOUR_PX)
                  const height = Math.max(24, bottom - top)
                  const w = 100 / lanes
                  return (
                    <div key={e.id} title={`${e.titolo}${e.dove ? ' · ' + e.dove : ''}`} style={{
                      position: 'absolute', top, height, left: `calc(${lane * w}% + 2px)`, width: `calc(${w}% - 4px)`,
                      background: 'var(--accent-soft, #e6efff)', color: 'var(--accent, #1e40af)',
                      borderLeft: '3px solid var(--accent, #2563eb)', borderRadius: 5, padding: '2px 5px',
                      fontSize: 11, lineHeight: 1.2, overflow: 'hidden', boxSizing: 'border-box' }}>
                      <div style={{ fontWeight: 600 }}>{fmtOra(e.inizio)}</div>
                      <div style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.titolo}</div>
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
        {loadingEv && <div className="pw-muted" style={{ fontSize: 11, padding: 6 }}>Aggiorno…</div>}
      </div>
    </div>
  )
}

export default function Calendario() {
  const { session } = useAuth()
  const { aziendaId } = useTenant()
  const tq = aziendaId ? `?azienda_id=${aziendaId}` : ''
  const [stato, setStato] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [week, setWeek] = useState(0)
  const [eventi, setEventi] = useState<any[]>([])
  const [loadingEv, setLoadingEv] = useState(false)
  const [calendari, setCalendari] = useState<any[]>([])

  const auth = { Authorization: `Bearer ${session?.access_token}` }

  async function caricaStato() {
    if (!API) { setLoading(false); return }
    try {
      const res = await fetch(`${API}/google/status${tq}`, { headers: auth })
      const data = await res.json()
      if (res.ok) setStato(data)
    } catch { /* la card GoogleConnect mostra gli errori */ } finally { setLoading(false) }
  }
  useEffect(() => { caricaStato() }, [aziendaId])

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

  async function caricaCalendari() {
    try {
      const res = await fetch(`${API}/google/calendari${tq}`, { headers: auth })
      const data = await res.json()
      if (res.ok) setCalendari(data.calendari || [])
    } catch { /* ignora */ }
  }
  useEffect(() => { if (stato?.connesso) caricaCalendari() }, [stato?.connesso])

  async function cambiaCalendario(id: string) {
    await fetch(`${API}/google/calendario${tq}`, {
      method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' },
      body: JSON.stringify({ calendar_id: id }),
    })
    await caricaCalendari()
    await caricaEventi()
  }

  const connesso = stato?.connesso
  const calSel = calendari.find(c => c.selezionato)?.id || stato?.calendar_id || 'primary'
  const giorni = Array.from({ length: 7 }, (_, i) => addDays(lunedi(week), i))
  const oggiKey = isoDay(new Date())
  const range = `${fmtGiorno(giorni[0])} – ${fmtGiorno(giorni[6])}`

  return (
    <div className="pw-stack" style={{ maxWidth: 1000 }}>
      <div>
        <div className="pw-eyebrow">Integrazioni</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Calendario</h1>
        <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>
          Vista settimanale del Google Calendar collegato. La connessione (e l'invio email) si gestisce
          qui sotto o dalla pagina <strong>Assistente</strong>.
        </div>
      </div>

      <GoogleConnect />

      {connesso && calendari.length > 0 && (
        <div className="pw-row" style={{ gap: 8, alignItems: 'center' }}>
          <span className="pw-muted" style={{ fontSize: 13 }}>Calendario:</span>
          <select className="pw-input pw-btn-sm" style={{ maxWidth: 320 }} value={calSel}
            onChange={e => cambiaCalendario(e.target.value)} title="Calendario usato per vista, disponibilità e meeting">
            {calendari.map(c => (
              <option key={c.id} value={c.id}>{c.nome}{c.primario ? ' (principale)' : ''}</option>
            ))}
          </select>
        </div>
      )}

      {!connesso && !loading && (
        <div className="pw-card"><div className="pw-card-body pw-muted">
          Collega Google qui sopra per vedere il calendario della settimana.
        </div></div>
      )}

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
            <GrigliaSettimana giorni={giorni} eventi={eventi} oggiKey={oggiKey} loadingEv={loadingEv} />
          </div>
        </div>
      )}
    </div>
  )
}
