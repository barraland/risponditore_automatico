import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { badgePriorita, badgeTicket, dataOra, lower, nomeContatto } from '../lib/format'
import Modal from '../components/Modal'
import { useTenant } from '../lib/tenant'

const VISTE: [string, string][] = [['ticket', 'Ticket'], ['chiamate', 'Chiamate / Log']]

// Etichetta + stile del canale di un'interazione (log multicanale).
function canaleBadge(canale?: string | null) {
  const c = lower(canale)
  if (c === 'whatsapp') return { label: 'WhatsApp', cls: 'ok' }
  if (c === 'mail') return { label: 'Mail', cls: 'mute' }
  return { label: 'Telefono', cls: 'cy' }   // 'voce' o default
}

function durata(sec?: number | null) {
  if (!sec && sec !== 0) return '—'
  const m = Math.floor(sec / 60), s = sec % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function TicketPage() {
  const [vista, setVista] = useState<'ticket' | 'chiamate'>('ticket')
  // Modali condivise fra le due viste (linking incrociato ticket <-> chiamata).
  const [apriTicket, setApriTicket] = useState<number | null>(null)
  const [apriChiamata, setApriChiamata] = useState<number | null>(null)
  const [refresh, setRefresh] = useState(0)

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div><div className="pw-eyebrow">Assistenza</div><h1 style={{ fontSize: 28, marginTop: 6 }}>Ticket &amp; chiamate</h1></div>
      </div>

      {/* Switch di vista, come nella schermata "Assistente". */}
      <div className="pw-row" style={{ gap: 6 }}>
        {VISTE.map(([k, lab]) => (
          <button key={k} className={`pw-btn pw-btn-sm ${vista === k ? 'pw-btn-primary' : 'pw-btn-ghost'}`}
            onClick={() => setVista(k as any)}>{lab}</button>
        ))}
      </div>

      {vista === 'ticket'
        ? <TicketView key={`t${refresh}`} onApri={setApriTicket} />
        : <ChiamateView key={`c${refresh}`} onApri={setApriChiamata} onApriTicket={setApriTicket} />}

      {apriTicket != null && (
        <TicketDettaglio id={apriTicket} onApriChiamata={(cid) => { setApriTicket(null); setApriChiamata(cid) }}
          onClose={() => setApriTicket(null)} onCambiato={() => { setApriTicket(null); setRefresh(r => r + 1) }} />
      )}
      {apriChiamata != null && (
        <ChiamataDettaglio id={apriChiamata} onApriTicket={(tid) => { setApriChiamata(null); setApriTicket(tid) }}
          onClose={() => setApriChiamata(null)} />
      )}
    </div>
  )
}

// ---------------- Vista TICKET ----------------

function TicketView({ onApri }: { onApri: (id: number) => void }) {
  const { aziendaId } = useTenant()
  const [righe, setRighe] = useState<any[]>([])
  const [stato, setStato] = useState('APERTO')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!aziendaId) { setLoading(false); return }
    supabase.from('ticket')
      .select('id, titolo, canale, priorita, stato, created_at, contatti(id, nome, cognome)')
      .eq('azienda_id', aziendaId)
      .order('created_at', { ascending: false })
      .then(({ data, error }) => {
        if (error) setErr(error.message); else setRighe(data || [])
        setLoading(false)
      })
  }, [aziendaId])

  const filtrate = stato ? righe.filter(r => r.stato === stato) : righe

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div className="pw-muted" style={{ fontSize: 14 }}>Segnalazioni di follow-up aperte dall'assistente o a mano.</div>
        <select className="pw-select" style={{ maxWidth: 200 }} value={stato} onChange={e => setStato(e.target.value)}>
          <option value="APERTO">Aperti</option>
          <option value="CHIUSO">Chiusi</option>
          <option value="">Tutti</option>
        </select>
      </div>
      <div className="pw-card">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : err ? <div className="pw-card-body"><div className="pw-error">{err}</div></div>
          : filtrate.length === 0 ? <div className="pw-empty">Nessun ticket.</div>
          : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Titolo</th><th>Contatto</th><th>Canale</th><th>Priorità</th><th>Aperto</th><th>Stato</th></tr></thead>
              <tbody>
                {filtrate.map(t => (
                  <tr key={t.id} onClick={() => onApri(t.id)}>
                    <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{t.titolo}</td>
                    <td>{t.contatti ? nomeContatto(t.contatti) : '—'}</td>
                    <td><span className={`pw-badge ${canaleBadge(t.canale).cls}`}>{canaleBadge(t.canale).label}</span></td>
                    <td>{t.priorita ? <span className={`pw-badge ${badgePriorita(t.priorita)}`}>{lower(t.priorita)}</span> : '—'}</td>
                    <td>{dataOra(t.created_at)}</td>
                    <td><span className={`pw-badge ${badgeTicket(t.stato)}`}>{lower(t.stato)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------- Vista CHIAMATE / LOG ----------------

function ChiamateView({ onApri, onApriTicket }: { onApri: (id: number) => void; onApriTicket: (id: number) => void }) {
  const { aziendaId } = useTenant()
  const [righe, setRighe] = useState<any[]>([])
  const [canale, setCanale] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!aziendaId) { setLoading(false); return }
    supabase.from('chiamate_voce')
      .select('id, canale, telefono, riassunto, durata_sec, iniziata_at, ticket_id, contatti(id, nome, cognome), ticket(id, titolo)')
      .eq('azienda_id', aziendaId)
      .order('iniziata_at', { ascending: false })
      .then(({ data, error }) => {
        if (error) setErr(error.message); else setRighe(data || [])
        setLoading(false)
      })
  }, [aziendaId])

  const filtrate = canale ? righe.filter(r => lower(r.canale) === canale || (canale === 'voce' && !r.canale)) : righe

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div className="pw-muted" style={{ fontSize: 14 }}>Ogni conversazione gestita (telefono / WhatsApp), collegata al suo ticket.</div>
        <select className="pw-select" style={{ maxWidth: 200 }} value={canale} onChange={e => setCanale(e.target.value)}>
          <option value="">Tutti i canali</option>
          <option value="voce">Telefono</option>
          <option value="whatsapp">WhatsApp</option>
          <option value="mail">Mail</option>
        </select>
      </div>
      <div className="pw-card">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : err ? <div className="pw-card-body"><div className="pw-error">{err}</div></div>
          : filtrate.length === 0 ? <div className="pw-empty">Nessuna conversazione registrata.</div>
          : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Canale</th><th>Contatto</th><th>Riassunto</th><th>Durata</th><th>Quando</th><th>Ticket</th></tr></thead>
              <tbody>
                {filtrate.map(c => (
                  <tr key={c.id} onClick={() => onApri(c.id)}>
                    <td><span className={`pw-badge ${canaleBadge(c.canale).cls}`}>{canaleBadge(c.canale).label}</span></td>
                    <td>{c.contatti ? nomeContatto(c.contatti) : (c.telefono || '—')}</td>
                    <td style={{ color: 'var(--fg-2)', maxWidth: 360 }}>
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {c.riassunto || '—'}
                      </div>
                    </td>
                    <td>{c.canale === 'whatsapp' ? '—' : durata(c.durata_sec)}</td>
                    <td>{dataOra(c.iniziata_at)}</td>
                    <td onClick={e => { if (c.ticket_id) { e.stopPropagation(); onApriTicket(c.ticket_id) } }}>
                      {c.ticket_id
                        ? <span className="pw-badge warn" style={{ cursor: 'pointer' }} title={c.ticket?.titolo || ''}>#{c.ticket_id} ↗</span>
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------- Dettaglio CHIAMATA ----------------

function ChiamataDettaglio({ id, onClose, onApriTicket }: { id: number; onClose: () => void; onApriTicket: (id: number) => void }) {
  const [c, setC] = useState<any>(null)

  useEffect(() => {
    supabase.from('chiamate_voce')
      .select('*, contatti(id, nome, cognome), ticket(id, titolo)')
      .eq('id', id).single()
      .then(({ data }) => setC(data))
  }, [id])

  if (!c) return <Modal title="Conversazione" width={860} onClose={onClose}><div className="pw-spinner">Caricamento…</div></Modal>
  const cb = canaleBadge(c.canale)

  return (
    <Modal title={`${cb.label} · ${c.contatti ? nomeContatto(c.contatti) : (c.telefono || '—')}`} width={860} onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Chiudi</button>
               {c.ticket_id && <button className="pw-btn pw-btn-primary" onClick={() => onApriTicket(c.ticket_id)}>Apri ticket collegato #{c.ticket_id}</button>}</>}>
      <div className="pw-row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className={`pw-badge ${cb.cls}`}>{cb.label}</span>
        {c.telefono && <span className="pw-muted" style={{ fontSize: 13 }}>{c.telefono}</span>}
        {c.contatti && <Link to={`/contatti/${c.contatti.id}`} onClick={onClose} style={{ fontSize: 13 }}>{nomeContatto(c.contatti)}</Link>}
      </div>
      <div className="pw-muted" style={{ fontSize: 12 }}>
        {dataOra(c.iniziata_at)}{c.canale !== 'whatsapp' && c.durata_sec != null ? ` · durata ${durata(c.durata_sec)}` : ''}
      </div>
      {c.riassunto && <div><div className="pw-muted" style={{ fontSize: 12 }}>Riassunto</div><div style={{ color: 'var(--fg-2)', fontSize: 14 }}>{c.riassunto}</div></div>}
      {c.ticket && (
        <div className="pw-muted" style={{ fontSize: 12 }}>
          Ticket collegato: <a style={{ cursor: 'pointer', color: 'var(--acc-cy, #6EE7FF)' }} onClick={() => onApriTicket(c.ticket_id)}>#{c.ticket.id} — {c.ticket.titolo}</a>
        </div>
      )}
      {c.trascrizione && (
        <details open>
          <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--acc-cy, #6EE7FF)' }}>Trascrizione</summary>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.55, color: 'var(--fg-2)', marginTop: 8, maxHeight: '55vh', overflow: 'auto' }}>{c.trascrizione}</pre>
        </details>
      )}
    </Modal>
  )
}

// ---------------- Dettaglio TICKET ----------------

function TicketDettaglio({ id, onClose, onCambiato, onApriChiamata }: {
  id: number; onClose: () => void; onCambiato: () => void; onApriChiamata: (id: number) => void
}) {
  const [t, setT] = useState<any>(null)
  const [chiamate, setChiamate] = useState<any[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    supabase.from('ticket')
      .select('*, contatti(id, nome, cognome), risposte_ticket(id, testo, inviata_email, created_at)')
      .eq('id', id).single()
      .then(({ data }) => setT(data))
    supabase.from('chiamate_voce')
      .select('id, canale, riassunto, iniziata_at')
      .eq('ticket_id', id)
      .order('iniziata_at', { ascending: false })
      .then(({ data }) => setChiamate(data || []))
  }, [id])

  async function cambiaStato() {
    setBusy(true)
    const nuovo = lower(t.stato) === 'aperto' ? 'CHIUSO' : 'APERTO'
    await supabase.from('ticket').update({ stato: nuovo }).eq('id', id)
    setBusy(false); onCambiato()
  }

  if (!t) return <Modal title="Ticket" width={860} onClose={onClose}><div className="pw-spinner">Caricamento…</div></Modal>
  const risposte = (t.risposte_ticket || []).sort((a: any, b: any) => (a.created_at || '').localeCompare(b.created_at || ''))
  const cb = canaleBadge(t.canale)

  return (
    <Modal title={t.titolo} width={860} onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Chiudi</button>
               <button className="pw-btn pw-btn-primary" disabled={busy} onClick={cambiaStato}>
                 {lower(t.stato) === 'aperto' ? 'Segna come chiuso' : 'Riapri'}</button></>}>
      <div className="pw-row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <span className={`pw-badge ${badgeTicket(t.stato)}`}>{lower(t.stato)}</span>
        {t.priorita && <span className={`pw-badge ${badgePriorita(t.priorita)}`}>priorità {lower(t.priorita)}</span>}
        <span className={`pw-badge ${cb.cls}`}>{cb.label}</span>
        {t.contatti && <Link to={`/contatti/${t.contatti.id}`} onClick={onClose} style={{ fontSize: 13 }}>{nomeContatto(t.contatti)}</Link>}
      </div>
      <div className="pw-muted" style={{ fontSize: 12 }}>Aperto il {dataOra(t.created_at)}</div>
      {t.descrizione && <div><div className="pw-muted" style={{ fontSize: 12 }}>Descrizione</div><div style={{ color: 'var(--fg-2)', fontSize: 14 }}>{t.descrizione}</div></div>}

      {chiamate.length > 0 && (
        <div>
          <div className="pw-muted" style={{ fontSize: 12, marginBottom: 6 }}>Conversazioni collegate ({chiamate.length})</div>
          <div className="pw-stack" style={{ gap: 6 }}>
            {chiamate.map((c: any) => {
              const b = canaleBadge(c.canale)
              return (
                <div key={c.id} onClick={() => onApriChiamata(c.id)}
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', fontSize: 13, cursor: 'pointer', display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span className={`pw-badge ${b.cls}`}>{b.label}</span>
                  <span style={{ color: 'var(--fg-2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.riassunto || '(nessun riassunto)'}</span>
                  <span className="pw-muted" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>{dataOra(c.iniziata_at)} ↗</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {t.storia && (
        <details>
          <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--acc-cy, #6EE7FF)' }}>Storia / trascrizione</summary>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.55, color: 'var(--fg-2)', marginTop: 8, maxHeight: '45vh', overflow: 'auto' }}>{t.storia}</pre>
        </details>
      )}
      {risposte.length > 0 && (
        <div><div className="pw-muted" style={{ fontSize: 12, marginBottom: 6 }}>Risposte ({risposte.length})</div>
          <div className="pw-stack" style={{ gap: 8 }}>
            {risposte.map((r: any) => (
              <div key={r.id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, fontSize: 13 }}>
                <div style={{ color: 'var(--fg-2)' }}>{r.testo}</div>
                <div className="pw-muted" style={{ fontSize: 11, marginTop: 4 }}>
                  {dataOra(r.created_at)}{r.inviata_email ? ' · inviata via email' : ''}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  )
}
