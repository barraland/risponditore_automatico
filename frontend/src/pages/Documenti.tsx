import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../lib/auth'
import { DOC_CATEGORIE, badgeDoc, dataBreve, fileSize, labelCategoria, lower, statoDoc } from '../lib/format'
import Modal from '../components/Modal'

const BUCKET = 'documenti'
const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

// Note in testo libero per categoria: il retriever le antepone SEMPRE quando consulta i
// documenti (es. "prezzi IVA inclusa", come leggere una colonna). Lista compatta, editor in modal.
function NoteCategorie() {
  const [note, setNote] = useState<Record<string, string>>({})
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<{ k: string; label: string } | null>(null)

  async function carica() {
    const { data } = await supabase.from('testi_categoria').select('categoria, testo')
    const m: Record<string, string> = {}
    for (const r of data || []) m[r.categoria] = r.testo || ''
    setNote(m)
  }
  useEffect(() => { carica() }, [])

  const preview = (t?: string) => {
    const s = (t || '').replace(/\s+/g, ' ').trim()
    return s ? (s.length > 90 ? s.slice(0, 90) + '…' : s) : '—'
  }

  return (
    <div className="pw-card">
      <div className="pw-card-head" style={{ cursor: 'pointer' }} onClick={() => setOpen(o => !o)}>
        <h3>Note per categoria <span className="pw-muted" style={{ fontWeight: 400, fontSize: 13 }}>— l'assistente le usa sempre</span></h3>
        <button className="pw-btn pw-btn-ghost pw-btn-sm">{open ? '▾' : '▸'}</button>
      </div>
      {open && (
        <div className="pw-card-body pw-stack" style={{ gap: 0 }}>
          <div className="pw-muted" style={{ fontSize: 13, marginBottom: 8 }}>
            Precisazioni corte legate ai file (es. “prezzi IVA inclusa”, come leggere una colonna).
            Per sconti e offerte usa <Link to="/assistente">Regole commerciali</Link>.
          </div>
          {DOC_CATEGORIE.map(([k, label]) => (
            <div key={k} className="pw-between" style={{ borderTop: '1px solid var(--border)', padding: '10px 0' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600, color: 'var(--fg)' }}>{label}</div>
                <div className="pw-muted" style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{preview(note[k])}</div>
              </div>
              <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setEditing({ k, label })}>Modifica</button>
            </div>
          ))}
        </div>
      )}
      {editing && (
        <EditNota cat={editing} value={note[editing.k] || ''}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); carica() }} />
      )}
    </div>
  )
}

function EditNota({ cat, value, onClose, onSaved }: {
  cat: { k: string; label: string }; value: string; onClose: () => void; onSaved: () => void
}) {
  const [testo, setTesto] = useState(value)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  async function salva() {
    setBusy(true); setErr(null)
    const { error } = await supabase.from('testi_categoria').upsert(
      { categoria: cat.k, testo: testo.trim() || null, aggiornato_at: new Date().toISOString() },
      { onConflict: 'categoria' })
    setBusy(false)
    if (error) setErr(error.message); else onSaved()
  }
  return (
    <Modal title={`Note — ${cat.label}`} onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button>
               <button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{busy ? 'Salvo…' : 'Salva'}</button></>}>
      <div className="pw-muted" style={{ fontSize: 13 }}>Testo aggiunto sempre quando l'assistente consulta i documenti di questa categoria.</div>
      <textarea className="pw-input" rows={10} style={{ resize: 'vertical', fontFamily: 'inherit' }}
        autoFocus value={testo} onChange={e => setTesto(e.target.value)} />
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}

export default function Documenti() {
  const { session } = useAuth()
  const [righe, setRighe] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [categoria, setCategoria] = useState('listino')
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  async function carica() {
    const { data, error } = await supabase.from('documenti')
      .select('id, nome_file, categoria, stato, dimensione, caricato_at, storage_path, errore, inviabile')
      .order('caricato_at', { ascending: false })
    if (error) setErr(error.message); else setRighe(data || [])
    setLoading(false)
  }

  async function toggleInviabile(r: any) {
    const nuovo = !r.inviabile
    setRighe(rs => rs.map(x => x.id === r.id ? { ...x, inviabile: nuovo } : x))  // ottimistico
    const { error } = await supabase.from('documenti').update({ inviabile: nuovo }).eq('id', r.id)
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
          <div className="pw-eyebrow">Base di conoscenza</div>
          <h1 style={{ fontSize: 28, marginTop: 6 }}>Documenti</h1>
          <div className="pw-muted" style={{ marginTop: 6, fontSize: 14 }}>
            Listini, schede, condizioni: una volta indicizzati, l'assistente li consulta su voce e WhatsApp.
          </div>
        </div>
        <div className="pw-row" style={{ gap: 8 }}>
          <Link to="/documenti/test" className="pw-btn pw-btn-ghost pw-btn-sm">🔎 Test agente retriever</Link>
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

      <NoteCategorie />

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : righe.length === 0 ? <div className="pw-empty">Nessun documento.</div>
          : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Nome</th><th>Categoria</th><th>Dimensione</th><th>Caricato</th><th>Stato</th><th>Inviabile</th><th></th></tr></thead>
              <tbody>
                {righe.map(r => (
                  <tr key={r.id} style={{ cursor: 'default' }}>
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
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => scarica(r)} disabled={!r.storage_path}>Scarica</button>{' '}
                      <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => elimina(r)}>Elimina</button>
                    </td>
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
