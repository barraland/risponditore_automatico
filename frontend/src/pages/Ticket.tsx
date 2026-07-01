import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { badgePriorita, badgeTicket, dataOra, lower, nomeContatto } from '../lib/format'
import Modal from '../components/Modal'
import { useTenant } from '../lib/tenant'

export default function TicketList() {
  const { aziendaId } = useTenant()
  const [righe, setRighe] = useState<any[]>([])
  const [stato, setStato] = useState('APERTO')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [apri, setApri] = useState<number | null>(null)

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    const { data, error } = await supabase.from('ticket')
      .select('id, titolo, canale, priorita, stato, created_at, contatti(id, nome, cognome)')
      .eq('azienda_id', aziendaId)
      .order('created_at', { ascending: false })
    if (error) setErr(error.message); else setRighe(data || [])
    setLoading(false)
  }
  useEffect(() => { carica() }, [])

  const filtrate = stato ? righe.filter(r => r.stato === stato) : righe

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div><div className="pw-eyebrow">Assistenza</div><h1 style={{ fontSize: 28, marginTop: 6 }}>Ticket</h1></div>
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
                  <tr key={t.id} onClick={() => setApri(t.id)}>
                    <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{t.titolo}</td>
                    <td>{t.contatti ? nomeContatto(t.contatti) : '—'}</td>
                    <td><span className="pw-badge mute">{lower(t.canale) || '—'}</span></td>
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
      {apri != null && <TicketDettaglio id={apri} onClose={() => setApri(null)} onCambiato={() => { setApri(null); carica() }} />}
    </div>
  )
}

function TicketDettaglio({ id, onClose, onCambiato }: { id: number; onClose: () => void; onCambiato: () => void }) {
  const [t, setT] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    supabase.from('ticket')
      .select('*, contatti(id, nome, cognome), risposte_ticket(id, testo, inviata_email, created_at)')
      .eq('id', id).single()
      .then(({ data }) => setT(data))
  }, [id])

  async function cambiaStato() {
    setBusy(true)
    const nuovo = lower(t.stato) === 'aperto' ? 'CHIUSO' : 'APERTO'
    await supabase.from('ticket').update({ stato: nuovo }).eq('id', id)
    setBusy(false); onCambiato()
  }

  if (!t) return <Modal title="Ticket" onClose={onClose}><div className="pw-spinner">Caricamento…</div></Modal>
  const risposte = (t.risposte_ticket || []).sort((a: any, b: any) => (a.created_at || '').localeCompare(b.created_at || ''))

  return (
    <Modal title={t.titolo} onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Chiudi</button>
               <button className="pw-btn pw-btn-primary" disabled={busy} onClick={cambiaStato}>
                 {lower(t.stato) === 'aperto' ? 'Segna come chiuso' : 'Riapri'}</button></>}>
      <div className="pw-row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <span className={`pw-badge ${badgeTicket(t.stato)}`}>{lower(t.stato)}</span>
        {t.priorita && <span className={`pw-badge ${badgePriorita(t.priorita)}`}>priorità {lower(t.priorita)}</span>}
        <span className="pw-badge mute">{lower(t.canale) || '—'}</span>
        {t.contatti && <Link to={`/contatti/${t.contatti.id}`} onClick={onClose} style={{ fontSize: 13 }}>{nomeContatto(t.contatti)}</Link>}
      </div>
      <div className="pw-muted" style={{ fontSize: 12 }}>Aperto il {dataOra(t.created_at)}</div>
      {t.descrizione && <div><div className="pw-muted" style={{ fontSize: 12 }}>Descrizione</div><div style={{ color: 'var(--fg-2)', fontSize: 14 }}>{t.descrizione}</div></div>}
      {t.storia && (
        <details>
          <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--acc-cy, #6EE7FF)' }}>Storia / trascrizione</summary>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12.5, color: 'var(--fg-2)', marginTop: 8, maxHeight: '30vh', overflow: 'auto' }}>{t.storia}</pre>
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
