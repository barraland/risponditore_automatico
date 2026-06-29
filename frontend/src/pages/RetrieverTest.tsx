import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { DOC_CATEGORIE, labelCategoria } from '../lib/format'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

type Chunk = { score: number; documento_id: number; documento: string; categoria: string; pagine: string | null; inviabile: boolean; estratto: string }

const FONTE_LABEL: Record<string, string> = { tabella: '📊 Tabella (CSV/Excel)', documenti: '📄 Documenti (PDF)', nessuna: '— Nessuna fonte' }

export default function RetrieverTest() {
  const { session } = useAuth()
  const [domanda, setDomanda] = useState('')
  const [categoria, setCategoria] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [risposta, setRisposta] = useState('')
  const [fonte, setFonte] = useState('')
  const [chunk, setChunk] = useState<Chunk[]>([])
  const [righe, setRighe] = useState<any[]>([])
  const [query, setQuery] = useState<any>(null)
  const [done, setDone] = useState(false)

  async function chiedi() {
    if (!domanda.trim()) return
    if (!API) { setErr('VITE_API_BASE non configurato: serve l\'URL del backend.'); return }
    setBusy(true); setErr(null); setRisposta(''); setChunk([]); setRighe([]); setQuery(null); setFonte(''); setDone(false)
    try {
      const res = await fetch(`${API}/api/retriever/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session?.access_token}` },
        body: JSON.stringify({ domanda: domanda.trim(), categoria: categoria || undefined }),
      })
      const data = await res.json()
      if (!res.ok) { setErr(data?.detail || 'Errore'); return }
      setRisposta(data.risposta || ''); setFonte(data.fonte || '')
      setChunk(data.chunk || []); setRighe(data.righe || []); setQuery(data.query || null); setDone(true)
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
          Fai una domanda in linguaggio naturale: il retriever decide da sé se rispondere dai
          DOCUMENTI (PDF) o da una TABELLA (CSV/Excel) e mostra come ci è arrivato.
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
              <label>Filtra documenti per categoria (opzionale)</label>
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

      {done && (
        <div className="pw-card">
          <div className="pw-card-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3>Risposta</h3>
            {fonte && <span className="pw-badge" title="Fonte scelta dal router">{FONTE_LABEL[fonte] || fonte}</span>}
          </div>
          <div className="pw-card-body" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
            {risposta || <span className="pw-muted">—</span>}
          </div>
        </div>
      )}

      {fonte === 'tabella' && (
        <div className="pw-card">
          <div className="pw-card-head"><h3>Dati tabellari</h3></div>
          <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
            {query && (
              <div className="pw-muted" style={{ fontSize: 12 }}>
                query: doc {query.documento_id} · filtri {JSON.stringify(query.filtri)}
                {query.order_by ? ` · order ${query.order_by} ${query.ascending ? '↑' : '↓'}` : ''}
              </div>
            )}
            {righe.length === 0
              ? <div className="pw-muted">Nessuna riga corrisponde ai filtri.</div>
              : (
                <div style={{ overflowX: 'auto' }}>
                  <table className="pw-table">
                    <thead><tr>{Object.keys(righe[0]).map((k) => <th key={k}>{k}</th>)}</tr></thead>
                    <tbody>
                      {righe.slice(0, 20).map((r: any, i: number) => (
                        <tr key={i}>{Object.keys(righe[0]).map((k) => <td key={k}>{String(r[k] ?? '')}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
          </div>
        </div>
      )}

      {fonte === 'documenti' && chunk.length > 0 && (
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
