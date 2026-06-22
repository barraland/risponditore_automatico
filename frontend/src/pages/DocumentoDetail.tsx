import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { badgeDoc, dataBreve, labelCategoria, statoDoc } from '../lib/format'

const BUCKET = 'documenti'
const PRE: React.CSSProperties = {
  whiteSpace: 'pre-wrap', fontFamily: 'var(--font-mono, monospace)', fontSize: 12.5,
  background: 'var(--bg-2, rgba(255,255,255,.03))', border: '1px solid var(--border)',
  borderRadius: 8, padding: 12, margin: 0, maxHeight: '40vh', overflow: 'auto', color: 'var(--fg-2)',
}

export default function DocumentoDetail() {
  const { id } = useParams()
  const [doc, setDoc] = useState<any>(null)
  const [sezioni, setSezioni] = useState<any[]>([])
  const [signedUrl, setSignedUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    (async () => {
      const { data: d, error } = await supabase.from('documenti').select('*').eq('id', id).single()
      if (error) { setErr(error.message); setLoading(false); return }
      setDoc(d)
      const { data: s } = await supabase.from('sezioni')
        .select('id, ordine, titolo, summary, page_start, page_end, contiene_tabelle, content_md')
        .eq('documento_id', id).order('ordine')
      setSezioni(s || [])
      if (d.storage_path) {
        const { data: su } = await supabase.storage.from(BUCKET).createSignedUrl(d.storage_path, 600)
        setSignedUrl(su?.signedUrl || null)
      }
      setLoading(false)
    })()
  }, [id])

  if (loading) return <div className="pw-spinner">Caricamento…</div>
  if (err) return <div className="pw-error">{err}</div>
  if (!doc) return <div className="pw-empty">Documento non trovato.</div>

  const isPdf = (doc.nome_file || '').toLowerCase().endsWith('.pdf')
  const anteprimaTesto = (sezioni[0]?.content_md || '').split('\n').slice(0, 20).join('\n')

  return (
    <div className="pw-stack">
      <Link to="/documenti" className="pw-btn pw-btn-ghost pw-btn-sm" style={{ width: 'fit-content' }}>← Documenti</Link>

      <div className="pw-between">
        <div>
          <h1 style={{ fontSize: 24 }}>{doc.nome_file}{' '}
            <span className={`pw-badge ${badgeDoc(doc.stato)}`} style={{ verticalAlign: 'middle' }}>{statoDoc(doc.stato)}</span>
          </h1>
          <div className="pw-muted" style={{ marginTop: 4 }}>
            {labelCategoria(doc.categoria)}{doc.n_pagine ? ` · ${doc.n_pagine} pagine` : ''}{doc.anno ? ` · ${doc.anno}` : ''} · caricato {dataBreve(doc.caricato_at)}
          </div>
        </div>
        {signedUrl && <a className="pw-btn pw-btn-ghost pw-btn-sm" href={signedUrl} target="_blank" rel="noreferrer">Apri il file</a>}
      </div>

      {doc.errore && <div className="pw-error">{doc.errore}</div>}

      <div className="pw-grid" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)' }}>
        {/* Anteprima file */}
        <div className="pw-card">
          <div className="pw-card-head"><h3>Anteprima</h3></div>
          <div className="pw-card-body">
            {isPdf && signedUrl ? (
              <iframe src={signedUrl} title="Anteprima PDF" style={{ width: '100%', height: '70vh', border: 0, borderRadius: 8 }} />
            ) : anteprimaTesto ? (
              <>
                <div className="pw-muted" style={{ fontSize: 12, marginBottom: 8 }}>Prime righe del contenuto estratto:</div>
                <pre style={PRE}>{anteprimaTesto}</pre>
              </>
            ) : (
              <div className="pw-muted">Anteprima non disponibile. {signedUrl && <a href={signedUrl} target="_blank" rel="noreferrer">Apri il file</a>}</div>
            )}
          </div>
        </div>

        {/* Indice generato dall'LLM */}
        <div className="pw-card">
          <div className="pw-card-head"><h3>Indice generato</h3>
            {sezioni.length > 0 && <span className="pw-badge mute">{sezioni.length} sezioni</span>}</div>
          <div className="pw-card-body pw-stack" style={{ gap: 10, maxHeight: '74vh', overflow: 'auto' }}>
            {sezioni.length > 0 ? sezioni.map(s => (
              <div key={s.id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12 }}>
                <div style={{ fontWeight: 600, color: 'var(--fg)' }}>{s.titolo}</div>
                <div className="pw-muted" style={{ fontSize: 12, marginTop: 2 }}>
                  pag. {s.page_start}–{s.page_end}
                  {s.contiene_tabelle && <span className="pw-badge cy" style={{ marginLeft: 8 }}>tabelle</span>}
                </div>
                {s.summary && <p style={{ fontSize: 13, fontStyle: 'italic', color: 'var(--fg-3)', margin: '8px 0 0' }}>{s.summary}</p>}
                {s.content_md && (
                  <details style={{ marginTop: 8 }}>
                    <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--acc-cy, #6EE7FF)' }}>Mostra testo della sezione</summary>
                    <pre style={{ ...PRE, marginTop: 8 }}>{s.content_md}</pre>
                  </details>
                )}
              </div>
            )) : doc.indice_raw ? (
              <>
                <div className="pw-muted" style={{ fontSize: 12 }}>Indice grezzo (non validato):</div>
                <pre style={PRE}>{doc.indice_raw}</pre>
              </>
            ) : doc.stato === 'processing' ? (
              <div className="pw-muted">In attesa dell'indicizzazione…</div>
            ) : (
              <div className="pw-muted">Nessun indice disponibile.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
