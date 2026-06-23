import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { STATI_ORDINE, badgeOrdine, dataBreve, euro, lower, nomeAgente, nomeContatto } from '../lib/format'

export default function OrdineDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const [o, setO] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [stato, setStato] = useState('')

  async function carica() {
    const { data, error } = await supabase.from('ordini')
      .select('*, locali(id, insegna), contatti(id, nome, cognome), agenti(id, nome, cognome), righe_ordine(id, descrizione, quantita, unita, prezzo_unitario)')
      .eq('id', id).single()
    if (error) setErr(error.message); else { setO(data); setStato(data.stato) }
    setLoading(false)
  }
  useEffect(() => { carica() }, [id])

  if (loading) return <div className="pw-spinner">Caricamento…</div>
  if (err) return <div className="pw-error">{err}</div>
  if (!o) return <div className="pw-empty">Ordine non trovato.</div>

  const righe = o.righe_ordine || []
  const tot = righe.reduce((s: number, r: any) => s + (r.prezzo_unitario ? (r.quantita || 0) * r.prezzo_unitario : 0), 0)

  async function salvaStato() { await supabase.from('ordini').update({ stato }).eq('id', id); carica() }
  async function elimina() {
    if (!confirm(`Eliminare l'ordine #${o.id}?`)) return
    await supabase.from('righe_ordine').delete().eq('ordine_id', id)
    await supabase.from('ordini').delete().eq('id', id)
    nav(o.locali ? `/societa/${o.locali.id}` : '/ordini')
  }

  return (
    <div className="pw-stack">
      <Link to={o.locali ? `/societa/${o.locali.id}` : '/ordini'} className="pw-btn pw-btn-ghost pw-btn-sm" style={{ width: 'fit-content' }}>← {o.locali?.insegna || 'Ordini'}</Link>
      <div className="pw-between">
        <div>
          <h1 style={{ fontSize: 24 }}>Ordine #{o.id} <span className={`pw-badge ${badgeOrdine(o.stato)}`} style={{ verticalAlign: 'middle' }}>{lower(o.stato)}</span></h1>
          <div className="pw-muted" style={{ marginTop: 4 }}>{o.locali?.insegna} · {dataBreve(o.data)} · <span style={{ textTransform: 'capitalize' }}>{lower(o.canale)}</span></div>
        </div>
      </div>

      <div className="pw-grid" style={{ gridTemplateColumns: 'minmax(0,1.6fr) minmax(0,1fr)' }}>
        <div className="pw-card">
          <div className="pw-card-head"><h3>Righe</h3></div>
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Prodotto</th><th>Qtà</th><th>Unità</th><th>Prezzo</th><th>Subtot.</th></tr></thead>
              <tbody>
                {righe.length === 0 ? <tr><td colSpan={5} className="pw-muted">Nessuna riga.</td></tr> :
                  righe.map((r: any) => (
                    <tr key={r.id} style={{ cursor: 'default' }}>
                      <td style={{ color: 'var(--fg)' }}>{r.descrizione}</td>
                      <td>{r.quantita ?? ''}</td><td>{r.unita || '—'}</td>
                      <td>{r.prezzo_unitario != null ? euro(r.prezzo_unitario) : '—'}</td>
                      <td>{r.prezzo_unitario != null ? euro((r.quantita || 0) * r.prezzo_unitario) : '—'}</td>
                    </tr>
                  ))}
              </tbody>
              <tfoot><tr><td colSpan={4} style={{ textAlign: 'right', color: 'var(--fg-3)' }}>Totale</td><td style={{ color: 'var(--fg)', fontWeight: 600 }}>{euro(tot)}</td></tr></tfoot>
            </table>
          </div>
        </div>

        <div className="pw-stack">
          <div className="pw-card"><div className="pw-card-body pw-stack" style={{ gap: 12, fontSize: 14 }}>
            <div><div className="pw-muted" style={{ fontSize: 12 }}>Origine</div><div style={{ textTransform: 'capitalize' }}>{lower(o.origine)}</div></div>
            <div><div className="pw-muted" style={{ fontSize: 12 }}>Referente</div><div>{o.contatti ? <Link to={`/contatti/${o.contatti.id}`}>{nomeContatto(o.contatti)}</Link> : '—'}</div></div>
            <div><div className="pw-muted" style={{ fontSize: 12 }}>Agente</div><div>{o.agenti ? <Link to={`/agenti/${o.agenti.id}`}>{nomeAgente(o.agenti)}</Link> : '—'}</div></div>
            {o.note && <div><div className="pw-muted" style={{ fontSize: 12 }}>Note</div><div style={{ whiteSpace: 'pre-wrap' }}>{o.note}</div></div>}
            {o.descrizione_agente && <div><div className="pw-muted" style={{ fontSize: 12 }}>Note agente</div><div>{o.descrizione_agente}</div></div>}
          </div></div>

          <div className="pw-card"><div className="pw-card-head"><h3>Stato</h3></div>
            <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
              <div className="pw-row" style={{ gap: 10 }}>
                <select className="pw-select" value={stato} onChange={e => setStato(e.target.value)}>{STATI_ORDINE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                <button className="pw-btn pw-btn-primary pw-btn-sm" onClick={salvaStato}>Aggiorna</button>
              </div>
              <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={elimina}>Elimina ordine</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
