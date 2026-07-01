import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { STATI_ORDINE, badgeOrdine, dataBreve, euro, lower } from '../lib/format'
import { useTenant } from '../lib/tenant'

const totale = (o: any) => (o.righe_ordine || []).reduce((s: number, r: any) => s + (r.prezzo_unitario ? (r.quantita || 0) * r.prezzo_unitario : 0), 0)

export default function OrdiniList() {
  const nav = useNavigate()
  const { aziendaId } = useTenant()
  const [righe, setRighe] = useState<any[]>([])
  const [stato, setStato] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!aziendaId) { setLoading(false); return }
    supabase.from('ordini')
      .select('id, data, canale, origine, stato, locali(id, insegna), righe_ordine(quantita, prezzo_unitario)')
      .eq('azienda_id', aziendaId)
      .order('data', { ascending: false })
      .then(({ data, error }) => { if (error) setErr(error.message); else setRighe(data || []); setLoading(false) })
  }, [aziendaId])

  const filtrate = stato ? righe.filter(r => r.stato === stato) : righe

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div><div className="pw-eyebrow">CRM HORECA</div><h1 style={{ fontSize: 28, marginTop: 6 }}>Ordini</h1></div>
        <select className="pw-select" style={{ maxWidth: 200 }} value={stato} onChange={e => setStato(e.target.value)}>
          <option value="">Tutti gli stati</option>{STATI_ORDINE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      <div className="pw-card">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : err ? <div className="pw-card-body"><div className="pw-error">{err}</div></div>
          : filtrate.length === 0 ? <div className="pw-empty">Nessun ordine.</div>
          : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>#</th><th>Società</th><th>Origine</th><th>Canale</th><th>Data</th><th>Totale</th><th>Stato</th></tr></thead>
              <tbody>
                {filtrate.map(o => (
                  <tr key={o.id} onClick={() => nav(`/ordini/${o.id}`)}>
                    <td>#{o.id}</td>
                    <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{o.locali?.insegna || '—'}</td>
                    <td style={{ textTransform: 'capitalize' }}>{lower(o.origine)}</td>
                    <td><span className="pw-badge mute">{lower(o.canale)}</span></td>
                    <td>{dataBreve(o.data)}</td>
                    <td>{euro(totale(o))}</td>
                    <td><span className={`pw-badge ${badgeOrdine(o.stato)}`}>{lower(o.stato)}</span></td>
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
