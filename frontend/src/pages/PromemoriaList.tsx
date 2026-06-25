import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { dataBreve, nomeContatto } from '../lib/format'

export default function PromemoriaList() {
  const [righe, setRighe] = useState<any[]>([])
  const [soloAttivi, setSoloAttivi] = useState(true)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  async function carica() {
    const { data, error } = await supabase.from('promemoria')
      .select('id, testo, scade_il, created_at, contatti(id, nome, cognome)')
      .order('created_at', { ascending: false })
    if (error) setErr(error.message); else setRighe(data || [])
    setLoading(false)
  }
  useEffect(() => { carica() }, [])

  async function elimina(id: number) {
    if (!confirm('Eliminare questo promemoria?')) return
    await supabase.from('promemoria').delete().eq('id', id); carica()
  }
  const scaduto = (s?: string) => (s ? new Date(s) < new Date() : false)
  const filtrate = soloAttivi ? righe.filter(r => !scaduto(r.scade_il)) : righe

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div><div className="pw-eyebrow">Risponditore</div><h1 style={{ fontSize: 28, marginTop: 6 }}>Promemoria</h1>
          <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>Note per cliente: l'assistente le comunica quando quel cliente chiama.</div>
        </div>
        <label className="pw-row" style={{ gap: 8, fontSize: 14 }}>
          <input type="checkbox" checked={soloAttivi} onChange={e => setSoloAttivi(e.target.checked)} /> solo attivi
        </label>
      </div>
      <div className="pw-card">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : err ? <div className="pw-card-body"><div className="pw-error">{err}</div></div>
          : filtrate.length === 0 ? <div className="pw-empty">Nessun promemoria.</div>
          : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Cliente</th><th>Promemoria</th><th>Scadenza</th><th></th></tr></thead>
              <tbody>
                {filtrate.map(r => (
                  <tr key={r.id} style={{ cursor: 'default', opacity: scaduto(r.scade_il) ? 0.5 : 1 }}>
                    <td style={{ fontWeight: 600 }}>
                      {r.contatti ? <Link to={`/contatti/${r.contatti.id}`}>{nomeContatto(r.contatti)}</Link> : '—'}
                    </td>
                    <td style={{ color: 'var(--fg-2)' }}>{r.testo}</td>
                    <td>{r.scade_il ? <span className={`pw-badge ${scaduto(r.scade_il) ? 'mute' : 'warn'}`}>{scaduto(r.scade_il) ? 'scaduto' : 'fino al'} {dataBreve(r.scade_il)}</span> : <span className="pw-muted">—</span>}</td>
                    <td style={{ textAlign: 'right' }}><button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => elimina(r.id)}>Elimina</button></td>
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
