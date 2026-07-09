import { Fragment, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../lib/auth'
import { useTenant } from '../lib/tenant'
import CampoAzienda from '../components/CampoAzienda'
import { DOC_CATEGORIE, badgeDoc, dataBreve, fileSize, labelCategoria, lower, statoDoc } from '../lib/format'

const BUCKET = 'documenti'
const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

// Nota interpretativa PER FILE: poche righe che spiegano cosa contiene il documento / come va letto.
// Il retriever la legge quando usa quel file. Salva su documenti.note (onBlur).
function NotaFile({ r, onSaved }: { r: any; onSaved: () => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  async function salva(v: string) {
    if (v.trim() === (r.note || '').trim()) return
    setBusy(true); setErr(null)
    const { error } = await supabase.from('documenti').update({ note: v.trim() || null }).eq('id', r.id)
    setBusy(false)
    if (error) setErr(error.message); else onSaved()
  }
  return (
    <div className="pw-row" style={{ gap: 8, alignItems: 'flex-start' }}>
      <span className="pw-muted" style={{ fontSize: 12, minWidth: 96, paddingTop: 6, flex: '0 0 auto' }}>Nota per l'AI</span>
      <div style={{ flex: 1 }}>
        <textarea className="pw-input" rows={2} defaultValue={r.note || ''} disabled={busy}
          placeholder="Testo interpretativo (poche righe): cosa contiene il file, come va letto, precisazioni. Il retriever lo legge quando usa questo documento."
          style={{ resize: 'vertical', fontFamily: 'inherit', fontSize: 13, width: '100%' }}
          onBlur={e => salva(e.target.value)} />
        {err && <div className="pw-error">{err}</div>}
      </div>
    </div>
  )
}

export default function Documenti() {
  const { session } = useAuth()
  const { aziendaId } = useTenant()
  const [righe, setRighe] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [categoria, setCategoria] = useState('listino')
  const [busy, setBusy] = useState(false)
  const [reindexing, setReindexing] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    const { data, error } = await supabase.from('documenti')
      .select('id, nome_file, categoria, stato, dimensione, caricato_at, storage_path, errore, inviabile, sempre_contesto, note')
      .eq('azienda_id', aziendaId)
      .order('caricato_at', { ascending: false })
    if (error) setErr(error.message); else setRighe(data || [])
    setLoading(false)
  }

  async function reindicizza() {
    if (!API) { setErr('VITE_API_BASE non configurato.'); return }
    setReindexing(true); setErr(null)
    try {
      const res = await fetch(`${API}/api/documenti/reindex`, {
        method: 'POST', headers: { Authorization: `Bearer ${session?.access_token}` },
      })
      const data = await res.json()
      if (!res.ok) { setErr(data?.detail || 'Errore'); return }
      const n = (data.reindicizzati || []).length
      const errori = (data.reindicizzati || []).filter((x: any) => x.errore).length
      alert(`Re-indicizzati ${n} documenti${errori ? ` (${errori} con errori)` : ''}.`)
    } catch (e: any) { setErr(e?.message || 'Errore di rete') } finally { setReindexing(false) }
  }

  async function toggleInviabile(r: any) {
    const nuovo = !r.inviabile
    setRighe(rs => rs.map(x => x.id === r.id ? { ...x, inviabile: nuovo } : x))  // ottimistico
    const { error } = await supabase.from('documenti').update({ inviabile: nuovo }).eq('id', r.id)
    if (error) { setErr(error.message); carica() }
  }

  async function toggleSempre(r: any) {
    const nuovo = !r.sempre_contesto
    setRighe(rs => rs.map(x => x.id === r.id ? { ...x, sempre_contesto: nuovo } : x))  // ottimistico
    const { error } = await supabase.from('documenti').update({ sempre_contesto: nuovo }).eq('id', r.id)
    if (error) { setErr(error.message); carica() }
  }
  useEffect(() => { carica() }, [])

  async function caricaFile(file: File) {
    if (!API) { setErr('VITE_API_BASE non configurato: serve l\'URL del backend per indicizzare.'); return }
    setBusy(true); setErr(null)
    try {
      const safe = file.name.replace(/[^\w.\-]+/g, '_')
      const path = `${Date.now()}_${safe}`
      const up = await supabase.storage.from(BUCKET).upload(path, file, { upsert: false })
      if (up.error) throw new Error('Storage: ' + up.error.message)

      const fd = new FormData()
      fd.append('categoria', categoria)
      fd.append('storage_path', path)
      if (aziendaId) fd.append('azienda_id', String(aziendaId))
      fd.append('file', file)
      const res = await fetch(`${API}/api/documenti`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session?.access_token}` },
        body: fd,
      })
      if (!res.ok) {
        const t = await res.text().catch(() => '')
        await supabase.storage.from(BUCKET).remove([path]) // rollback file orfano
        throw new Error(`Indicizzazione fallita (${res.status}): ${t.slice(0, 200)}`)
      }
      await carica()
    } catch (e: any) {
      setErr(e.message || String(e))
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function scarica(r: any) {
    if (!r.storage_path) { setErr('File originale non disponibile su Storage.'); return }
    const { data, error } = await supabase.storage.from(BUCKET).createSignedUrl(r.storage_path, 60)
    if (error) setErr(error.message); else window.open(data.signedUrl, '_blank')
  }

  async function elimina(r: any) {
    if (!confirm(`Eliminare "${r.nome_file}"?`)) return
    await supabase.from('sezioni').delete().eq('documento_id', r.id)
    if (r.storage_path) await supabase.storage.from(BUCKET).remove([r.storage_path])
    const { error } = await supabase.from('documenti').delete().eq('id', r.id)
    if (error) setErr(error.message); else carica()
  }

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div>
          <div className="pw-eyebrow">Assistente</div>
          <h1 style={{ fontSize: 28, marginTop: 6 }}>Base di Conoscenza</h1>
          <div className="pw-muted" style={{ marginTop: 6, fontSize: 14 }}>
            Listini, schede, condizioni: una volta indicizzati, l'assistente li consulta su voce e WhatsApp.
          </div>
        </div>
        <div className="pw-row" style={{ gap: 8 }}>
          <Link to="/documenti/test" className="pw-btn pw-btn-ghost pw-btn-sm">🔎 Test agente retriever</Link>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" disabled={reindexing} onClick={reindicizza}>
            {reindexing ? 'Indicizzo…' : '⟳ Re-indicizza'}
          </button>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={carica}>↻ Aggiorna</button>
        </div>
      </div>

      <div className="pw-card">
        <div className="pw-card-head"><h3>Carica un documento</h3></div>
        <div className="pw-card-body pw-row" style={{ gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="pw-field" style={{ flex: 1, minWidth: 200 }}>
            <label>Categoria</label>
            <select className="pw-select" value={categoria} onChange={e => setCategoria(e.target.value)}>
              {DOC_CATEGORIE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.xls" disabled={busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) caricaFile(f) }}
            style={{ flex: 2, minWidth: 240, color: 'var(--fg-3)', fontSize: 14 }} />
          {busy && <span className="pw-badge warn">caricamento…</span>}
        </div>
        <div className="pw-card-body" style={{ paddingTop: 0 }}>
          <div className="pw-muted" style={{ fontSize: 12 }}>Formati: PDF, DOCX, TXT, MD, CSV, XLSX. I PDF vengono indicizzati in background (stato "in elaborazione" → "pronto").</div>
        </div>
      </div>

      <CampoAzienda campo="descrizione_servizi" titolo="Cosa offriamo"
        hint="Prodotti/servizi, dove operate, tempi di consegna, ordine minimo… Info sul catalogo che l'assistente usa per rispondere."
        rows={6} />
      <CampoAzienda campo="regole_commerciali" titolo="Regole commerciali e promozioni"
        hint="Prezzi, sconti e offerte che l'assistente applica sempre — sui prezzi e negli ordini. Es: «compri 10 casse, 5 in omaggio»; «3x2 sui pelati fino al 14/08/2026»; «sconto 10% sopra i 500€». Per calcoli ambigui chiede conferma."
        rows={6} />

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : righe.length === 0 ? <div className="pw-empty">Nessun documento.</div>
          : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Nome</th><th>Categoria</th><th>Dimensione</th><th>Caricato</th><th>Stato</th><th>Inviabile</th><th>Sempre presente</th><th></th></tr></thead>
              <tbody>
                {righe.map(r => (
                  <Fragment key={r.id}>
                    <tr style={{ cursor: 'default' }}>
                      <td style={{ fontWeight: 600 }}><Link to={`/documenti/${r.id}`}>{r.nome_file}</Link></td>
                      <td>{labelCategoria(r.categoria)}</td>
                      <td>{fileSize(r.dimensione)}</td>
                      <td>{dataBreve(r.caricato_at)}</td>
                      <td>
                        <span className={`pw-badge ${badgeDoc(r.stato)}`} title={r.errore || ''}>{statoDoc(r.stato)}</span>
                      </td>
                      <td>
                        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 13 }}
                          title="Se attivo, l'assistente può inviare questo documento al cliente come allegato">
                          <input type="checkbox" checked={r.inviabile !== false} onChange={() => toggleInviabile(r)} />
                          {r.inviabile !== false ? 'Sì' : 'No'}
                        </label>
                      </td>
                      <td>
                        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 13 }}
                          title="Se attivo, l'assistente ha SEMPRE il testo di questo documento nel prompt, in ogni conversazione — ED ESCE dalla ricerca automatica (retriever). Usalo SOLO per documenti BREVI e importanti (poche pagine: regole, orari, listino sintetico). Per FAQ/cataloghi/guide lunghe lascialo su 'No': il retriever ne recupera solo i pezzi pertinenti.">
                          <input type="checkbox" checked={r.sempre_contesto === true} onChange={() => toggleSempre(r)} />
                          {r.sempre_contesto === true ? 'Sì' : 'No'}
                          {r.sempre_contesto === true && (r.dimensione || 0) > 40000 &&
                            <span className="pw-badge warn" title="Documento grande: 'sempre presente' inietta il testo in OGNI messaggio e viene troncato oltre il limite. Meglio lasciarlo al retriever (metti 'No').">⚠ grande</span>}
                        </label>
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => scarica(r)} disabled={!r.storage_path}>Scarica</button>{' '}
                        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => elimina(r)}>Elimina</button>
                      </td>
                    </tr>
                    <tr>
                      <td colSpan={8} style={{ paddingTop: 0, borderTop: 'none' }}>
                        <NotaFile r={r} onSaved={carica} />
                      </td>
                    </tr>
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
