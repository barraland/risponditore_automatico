import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { badgeStato, dataBreve, euro, lower, nomeAgente } from '../lib/format'

const totale = (o: any) => (o.righe_ordine || []).reduce((s: number, r: any) => s + (r.prezzo_unitario ? (r.quantita || 0) * r.prezzo_unitario : 0), 0)

export default function AgenteDetail() {
  const { id } = useParams()
  const [a, setA] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    supabase.from('agenti')
      .select('*, locali(id, insegna, stato_relazione), ordini(id, data, stato, locali(id, insegna), righe_ordine(quantita, prezzo_unitario))')
      .eq('id', id).single()
      .then(({ data, error }) => { if (error) setErr(error.message); else setA(data); setLoading(false) })
  }, [id])

  if (loading) return <div className="pw-spinner">Caricamento…</div>
  if (err) return <div className="pw-error">{err}</div>
  if (!a) return <div className="pw-empty">Agente non trovato.</div>

  const ordini = a.ordini || []
  const fatturato = ordini.filter((o: any) => ['confermato', 'evaso'].includes(lower(o.stato))).reduce((s: number, o: any) => s + totale(o), 0)
  const provv = fatturato * (a.percentuale_provvigione || 0) / 100

  return (
    <div className="pw-stack">
      <Link to="/agenti" className="pw-btn pw-btn-ghost pw-btn-sm" style={{ width: 'fit-content' }}>← Agenti</Link>
      <div>
        <h1 style={{ fontSize: 26 }}>{nomeAgente(a)}</h1>
        <div className="pw-muted" style={{ marginTop: 4 }}>{a.zona || '—'}{a.telefono ? ` · ${a.telefono}` : ''}{a.email ? ` · ${a.email}` : ''}</div>
      </div>

      <div className="pw-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        <Stat label="Società in portafoglio" value={String((a.locali || []).length)} />
        <Stat label="Fatturato ordini" value={euro(fatturato)} />
        <Stat label={`Provvigione stimata${a.percentuale_provvigione ? ` (${a.percentuale_provvigione}%)` : ''}`} value={euro(provv)} />
      </div>

      <div className="pw-grid" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.4fr)' }}>
        <div className="pw-card">
          <div className="pw-card-head"><h3>Portafoglio</h3></div>
          <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
            {(a.locali || []).length === 0 && <div className="pw-muted">Nessuna società.</div>}
            {(a.locali || []).map((l: any) => (
              <div key={l.id} className="pw-between" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
                <Link to={`/societa/${l.id}`}>{l.insegna}</Link>
                <span className={`pw-badge ${badgeStato(l.stato_relazione)}`}>{lower(l.stato_relazione)}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="pw-card">
          <div className="pw-card-head"><h3>Ordini attribuiti ({ordini.length})</h3></div>
          {ordini.length === 0 ? <div className="pw-empty">Nessun ordine.</div> : (
            <div style={{ overflowX: 'auto' }}>
              <table className="pw-table">
                <thead><tr><th>#</th><th>Società</th><th>Data</th><th>Totale</th><th>Stato</th></tr></thead>
                <tbody>
                  {ordini.map((o: any) => (
                    <tr key={o.id} onClick={() => location.assign(`/ordini/${o.id}`)}>
                      <td>#{o.id}</td><td>{o.locali?.insegna || '—'}</td><td>{dataBreve(o.data)}</td>
                      <td>{euro(totale(o))}</td><td><span className="pw-badge mute">{lower(o.stato)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="pw-card"><div className="pw-card-body" style={{ textAlign: 'center' }}>
      <div className="pw-muted" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--fg)', marginTop: 6 }}>{value}</div>
    </div></div>
  )
}
