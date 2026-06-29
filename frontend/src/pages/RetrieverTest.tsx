import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { DOC_CATEGORIE, labelCategoria } from '../lib/format'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

type Chunk = { score: number; documento_id: number; documento: string; categoria: string; pagine: string | null; inviabile: boolean; estratto: string }

export default function RetrieverTest() {
  const { session } = useAuth()
  const [domanda, setDomanda] = useState('')
  const [categoria, setCategoria] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [risposta, setRisposta] = useState('')
  const [chunk, setChunk] = useState<Chunk[]>([])
  const [tab, setTab] = useState<any>(null)

  async function chiedi() {
    if (!domanda.trim()) return
    if (!API) { setErr('VITE_API_BASE non configurato: serve l\'URL del backend.'); return }
    setBusy(true); setErr(null); setRisposta(''); setChunk([]); setTab(null)
    try {
      const res = await fetch(`${API}/api/retriever/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session?.access_token}` },
        body: JSON.stringify({ domanda: domanda.trim(), categoria: categoria || undefined }),
      })
      const data = await res.json()
      if (!res.ok) { setErr(data?.detail || 'Errore'); return }
      setRisposta(data.risposta || '')
      setChunk(data.chunk || [])
      setTab(data.tabellare || null)
      if (data.errore) setErr(`(${data.errore})`)
    } catch (e: any) {
      setErr(e?.message || 'Errore di rete')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="pw-stack" style={{ maxWidth: 900 }}>
      <div>
        <div className="pw-eyebrow"><Link to="/documenti">Documenti</Link> · Test</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Test agente retriever</h1>
        <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>
          Fai una domanda in linguaggio naturale: l'agente cerca nei chunk indicizzati (ricerca
          semantica) e risponde citando le fonti. Sotto vedi i pezzi recuperati e il punteggio.
        </div>
      </div>

      <div className="pw-card">
        <div className="pw-card-body pw-stack" style={{ gap: 12 }}>
          <div className="pw-field">
            <label>Domanda</label>
            <textarea className="pw-input" rows={3} style={{ resize: 'vertical', fontFamily: 'inherit' }}
              placeholder="Es: qual è il termine di pagamento? avete succhi sotto i 2 euro?"
              value={domanda} onChange={e => setDomanda(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) chiedi() }} />
          </div>
          <div className="pw-row" style={{ gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div className="pw-field" style={{ minWidth: 220 }}>
              <label>Filtra per categoria (opzionale)</label>
              <select className="pw-input" value={categoria} onChange={e => setCategoria(e.target.value)}>
                <option value="">Tutte</option>
                {DOC_CATEGORIE.map(([val, lab]) => <option key={val} value={val}>{lab}</option>)}
              </select>
            </div>
            <button className="pw-btn pw-btn-primary" disabled={busy} onClick={chiedi}>
              {busy ? 'Cerco…' : 'Chiedi'}
            </button>
          </div>
          {err && <div className="pw-error">{err}</div>}
        </div>
      </div>

      {risposta && (
        <div className="pw-card">
          <div className="pw-card-head"><h3>Risposta</h3></div>
          <div className="pw-card-body" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{risposta}</div>
        </div>
      )}

      {tab && (
        <div className="pw-card">
          <div className="pw-card-head"><h3>Risultato tabellare (CSV/Excel)</h3></div>
          <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
            {tab.errore === 'no_tables' && <div className="pw-muted">Nessun file tabellare (CSV/Excel) indicizzato. Carica un CSV/Excel e riprova.</div>}
            {tab.errore === 'no_match' && <div className="pw-muted">L'agente non ha individuato una tabella pertinente a questa domanda.</div>}
            {!tab.errore && (!tab.righe || tab.righe.length === 0) && <div className="pw-muted">Tabella selezionata, ma nessuna riga corrisponde ai filtri.</div>}
            {tab.risposta && <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{tab.risposta}</div>}
            {tab.query && (
              <div className="pw-muted" style={{ fontSize: 12 }}>
                query: doc {tab.query.documento_id} · filtri {JSON.stringify(tab.query.filtri)}
                {tab.query.order_by ? ` · order ${tab.query.order_by} ${tab.query.ascending ? '↑' : '↓'}` : ''}
              </div>
            )}
            {tab.righe && tab.righe.length > 0 && (
              <div style={{ overflowX: 'auto' }}>
                <table className="pw-table">
                  <thead><tr>{Object.keys(tab.righe[0]).map((k: string) => <th key={k}>{k}</th>)}</tr></thead>
                  <tbody>
                    {tab.righe.slice(0, 20).map((r: any, i: number) => (
                      <tr key={i}>{Object.keys(tab.righe[0]).map((k: string) => <td key={k}>{String(r[k] ?? '')}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {chunk.length > 0 && (
        <div className="pw-card">
          <div className="pw-card-head"><h3>Chunk recuperati ({chunk.length})</h3></div>
          <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
            {chunk.map((c, i) => (
              <div key={i} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10 }}>
                <div className="pw-row" style={{ justifyContent: 'space-between', fontSize: 13, marginBottom: 6 }}>
                  <span style={{ fontWeight: 600, color: 'var(--fg)' }}>
                    {c.documento}{c.pagine ? ` · pp. ${c.pagine}` : ''}
                    <span title="Inviabile al cliente come allegato" style={{ marginLeft: 8, fontSize: 11, fontWeight: 500,
                      color: c.inviabile ? 'var(--ok, #2e7d32)' : 'var(--muted, #888)' }}>
                      {c.inviabile ? '📎 inviabile' : '🔒 non inviabile'}
                    </span>
                  </span>
                  <span className="pw-muted">{labelCategoria(c.categoria)} · score {c.score}</span>
                </div>
                <div style={{ color: 'var(--fg-2)', fontSize: 13 }}>{c.estratto}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
