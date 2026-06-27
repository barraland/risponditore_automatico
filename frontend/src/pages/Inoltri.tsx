import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'

const VUOTO = { nome: '', cognome: '', ruolo: '', email: '', telefono: '', regole: '' }

export default function Inoltri() {
  const [righe, setRighe] = useState<any[]>([])
  const [f, setF] = useState({ ...VUOTO })
  const [editId, setEditId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const set = (k: string, v: string) => setF({ ...f, [k]: v })

  async function carica() {
    const { data, error } = await supabase.from('inoltri')
      .select('id, nome, cognome, ruolo, email, telefono, regole').order('created_at', { ascending: false })
    if (error) setErr(error.message); else setRighe(data || [])
  }
  useEffect(() => { carica() }, [])

  function modifica(r: any) {
    setEditId(r.id)
    setF({ nome: r.nome || '', cognome: r.cognome || '', ruolo: r.ruolo || '', email: r.email || '', telefono: r.telefono || '', regole: r.regole || '' })
    setErr(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  function annulla() { setEditId(null); setF({ ...VUOTO }); setErr(null) }

  async function salva() {
    if (!f.telefono.trim()) { setErr('Il telefono è obbligatorio.'); return }
    setBusy(true); setErr(null)
    const payload = {
      nome: f.nome.trim() || null, cognome: f.cognome.trim() || null, ruolo: f.ruolo.trim() || null,
      email: f.email.trim() || null, telefono: f.telefono.trim(), regole: f.regole.trim() || null,
    }
    const { error } = editId == null
      ? await supabase.from('inoltri').insert(payload)
      : await supabase.from('inoltri').update(payload).eq('id', editId)
    setBusy(false)
    if (error) setErr(error.message); else { setEditId(null); setF({ ...VUOTO }); carica() }
  }
  async function elimina(id: number) {
    if (!confirm('Rimuovere questo destinatario di inoltro?')) return
    if (editId === id) annulla()
    await supabase.from('inoltri').delete().eq('id', id); carica()
  }

  return (
    <div className="pw-stack" style={{ maxWidth: 900 }}>
      <div>
        <div className="pw-eyebrow">Risponditore</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Inoltri chiamata</h1>
        <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>
          Persone a cui l'assistente può passare la chiamata. Le regole spiegano QUANDO inoltrare a
          ciascuno (es. «spedizioni e consegne»). L'assistente le vede sempre nel prompt.
        </div>
      </div>

      <div className="pw-card">
        <div className="pw-card-head"><h3>{editId == null ? 'Aggiungi destinatario' : 'Modifica destinatario'}</h3></div>
        <div className="pw-card-body pw-stack" style={{ gap: 12 }}>
          <div className="pw-row" style={{ gap: 12, flexWrap: 'wrap' }}>
            <div className="pw-field" style={{ flex: 1, minWidth: 140 }}><label>Nome</label><input className="pw-input" value={f.nome} onChange={e => set('nome', e.target.value)} /></div>
            <div className="pw-field" style={{ flex: 1, minWidth: 140 }}><label>Cognome</label><input className="pw-input" value={f.cognome} onChange={e => set('cognome', e.target.value)} /></div>
            <div className="pw-field" style={{ flex: 1, minWidth: 140 }}><label>Ruolo</label><input className="pw-input" placeholder="es. Spedizioni" value={f.ruolo} onChange={e => set('ruolo', e.target.value)} /></div>
          </div>
          <div className="pw-row" style={{ gap: 12, flexWrap: 'wrap' }}>
            <div className="pw-field" style={{ flex: 1, minWidth: 140 }}><label>Telefono *</label><input className="pw-input" placeholder="+39…" value={f.telefono} onChange={e => set('telefono', e.target.value)} /></div>
            <div className="pw-field" style={{ flex: 1, minWidth: 140 }}><label>Email</label><input className="pw-input" value={f.email} onChange={e => set('email', e.target.value)} /></div>
          </div>
          <div className="pw-field"><label>Regole di inoltro</label>
            <textarea className="pw-input" rows={2} style={{ resize: 'vertical', fontFamily: 'inherit' }}
              placeholder="Quando inoltrare a questa persona, es: «questioni di spedizione, consegne, resi»"
              value={f.regole} onChange={e => set('regole', e.target.value)} /></div>
          {err && <div className="pw-error">{err}</div>}
          <div className="pw-row" style={{ justifyContent: 'flex-end', gap: 8 }}>
            {editId != null && <button className="pw-btn pw-btn-ghost" disabled={busy} onClick={annulla}>Annulla</button>}
            <button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{editId == null ? 'Aggiungi' : 'Salva modifiche'}</button>
          </div>
        </div>
      </div>

      <div className="pw-card">
        {righe.length === 0 ? <div className="pw-empty">Nessun destinatario di inoltro.</div> : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Nome</th><th>Ruolo</th><th>Telefono</th><th>Regole di inoltro</th><th></th></tr></thead>
              <tbody>
                {righe.map(r => (
                  <tr key={r.id} style={{ cursor: 'default' }}>
                    <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{`${r.nome || ''} ${r.cognome || ''}`.trim() || '—'}</td>
                    <td>{r.ruolo || '—'}</td>
                    <td>{r.telefono}</td>
                    <td style={{ color: 'var(--fg-2)', fontSize: 13 }}>{r.regole || '—'}</td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => modifica(r)}>Modifica</button>
                      <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => elimina(r.id)}>Elimina</button>
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
