import { Fragment, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'
import { useCommercioLabels } from '../lib/commercioLabels'
import { dataOra, nomeContatto } from '../lib/format'

type Riga = { id: number; quantity: number; unit_price: number | string | null; catalog_items: { name: string } | null }
type Ordine = {
  id: number; created_at: string | null; status: string | null; total: number | string | null; notes: string | null
  contatti: { id: number; nome: string | null; cognome: string | null } | null
  order_items: Riga[]
}

const euro = (v: number | string | null) => (v == null || v === '' ? '—' : `€ ${Number(v).toFixed(2)}`)

// Lo status è salvato in maiuscolo (nome enum): DRAFT/CONFIRMED/COMPLETED/CANCELLED.
const STATO: Record<string, { label: string; cls: string }> = {
  draft: { label: 'Bozza', cls: 'mute' },
  confirmed: { label: 'Confermato', cls: 'cy' },
  completed: { label: 'Completato', cls: 'ok' },
  cancelled: { label: 'Annullato', cls: 'warn' },
}
function badgeStato(status: string | null) {
  const s = STATO[(status || '').toLowerCase()] || { label: status || '—', cls: 'mute' }
  return <span className={`pw-badge ${s.cls}`}>{s.label}</span>
}

export default function Ordini() {
  const { aziendaId } = useTenant()
  const [labels] = useCommercioLabels(aziendaId)
  const [righe, setRighe] = useState<Ordine[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [aperto, setAperto] = useState<number | null>(null)

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    setLoading(true); setErr(null)
    const { data, error } = await supabase.from('orders')
      .select('id, created_at, status, total, notes, contatti(id, nome, cognome),' +
        ' order_items(id, quantity, unit_price, catalog_items(name))')
      .eq('azienda_id', aziendaId).order('created_at', { ascending: false })
    if (error) setErr(error.message); else setRighe((data as any) || [])
    setLoading(false)
  }
  useEffect(() => { carica() }, [aziendaId])

  if (loading) return <div className="pw-spinner">Caricamento…</div>

  return (
    <div className="pw-stack">
      <div>
        <div className="pw-eyebrow">CRM</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>{labels.ordine.plur}</h1>
        <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>
          Gli {labels.ordine.plur.toLowerCase()} dei clienti. Clicca su una riga per vedere le {labels.riga.plur.toLowerCase()}.
        </div>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        {righe.length === 0
          ? <div className="pw-empty">Nessun/a «{labels.ordine.sing}» ancora registrato/a.</div>
          : (
            <div style={{ overflowX: 'auto' }}>
              <table className="pw-table">
                <thead><tr>
                  <th style={{ width: 28 }}></th><th>Contatto</th><th>Data</th><th>Stato</th>
                  <th>{labels.riga.plur}</th><th style={{ textAlign: 'right' }}>Totale</th>
                </tr></thead>
                <tbody>
                  {righe.map(o => {
                    const open = aperto === o.id
                    const tot = o.total != null ? o.total : (o.order_items || [])
                      .reduce((s, r) => s + (r.unit_price != null ? Number(r.unit_price) * (r.quantity || 0) : 0), 0)
                    return (
                      <Fragment key={o.id}>
                        <tr onClick={() => setAperto(open ? null : o.id)} style={{ cursor: 'pointer' }}>
                          <td style={{ color: 'var(--fg-3)' }}>{open ? '▾' : '▸'}</td>
                          <td style={{ fontWeight: 600, color: 'var(--fg)' }}>
                            {o.contatti ? <Link to={`/contatti/${o.contatti.id}`} onClick={e => e.stopPropagation()}>{nomeContatto(o.contatti)}</Link> : '—'}
                          </td>
                          <td>{dataOra(o.created_at)}</td>
                          <td>{badgeStato(o.status)}</td>
                          <td>{(o.order_items || []).length}</td>
                          <td style={{ textAlign: 'right', whiteSpace: 'nowrap', fontWeight: 600, color: 'var(--fg)' }}>{euro(tot)}</td>
                        </tr>
                        {open && (
                          <tr key={`${o.id}-d`}>
                            <td></td>
                            <td colSpan={5} style={{ background: 'var(--bg-soft)' }}>
                              <div className="pw-stack" style={{ gap: 8, padding: '6px 0 10px' }}>
                                <table className="pw-table" style={{ background: 'transparent' }}>
                                  <thead><tr>
                                    <th>{labels.catalogo.sing}</th><th style={{ textAlign: 'right' }}>Qtà</th>
                                    <th style={{ textAlign: 'right' }}>Prezzo unit.</th><th style={{ textAlign: 'right' }}>Subtotale</th>
                                  </tr></thead>
                                  <tbody>
                                    {(o.order_items || []).map(r => (
                                      <tr key={r.id} style={{ cursor: 'default' }}>
                                        <td style={{ color: 'var(--fg)' }}>{r.catalog_items?.name || '—'}</td>
                                        <td style={{ textAlign: 'right' }}>{r.quantity}</td>
                                        <td style={{ textAlign: 'right' }}>{euro(r.unit_price)}</td>
                                        <td style={{ textAlign: 'right' }}>{euro(r.unit_price != null ? Number(r.unit_price) * (r.quantity || 0) : null)}</td>
                                      </tr>
                                    ))}
                                    {(o.order_items || []).length === 0 && (
                                      <tr style={{ cursor: 'default' }}><td colSpan={4} className="pw-muted">Nessuna riga.</td></tr>
                                    )}
                                  </tbody>
                                </table>
                                {o.notes && <div className="pw-muted" style={{ fontSize: 13 }}>Note: {o.notes}</div>}
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
      </div>
    </div>
  )
}
