import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'

const PRIORITA: [string, string][] = [['inoltra_alta', 'Alta'], ['inoltra_media', 'Media'], ['inoltra_bassa', 'Bassa']]

export default function Admin() {
  const [righe, setRighe] = useState<any[]>([])
  const [nome, setNome] = useState('')
  const [telefono, setTelefono] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function carica() {
    const { data, error } = await supabase.from('amministratori')
      .select('id, nome, telefono, email, inoltra_alta, inoltra_media, inoltra_bassa, created_at')
      .order('created_at', { ascending: false })
    if (error) setErr(error.message); else setRighe(data || [])
  }
  useEffect(() => { carica() }, [])

  async function aggiungi() {
    if (!telefono.trim()) { setErr('Inserisci un numero di telefono.'); return }
    setBusy(true); setErr(null)
    const { error } = await supabase.from('amministratori')
      .insert({ nome: nome.trim() || null, telefono: telefono.trim(), email: email.trim() || null })
    setBusy(false)
    if (error) setErr(error.message); else { setNome(''); setTelefono(''); setEmail(''); carica() }
  }
  async function elimina(id: number) {
    if (!confirm('Rimuovere questo amministratore?')) return
    await supabase.from('amministratori').delete().eq('id', id); carica()
  }
  async function toggleFlag(a: any, campo: string) {
    const nuovo = !a[campo]
    setRighe(rs => rs.map(x => x.id === a.id ? { ...x, [campo]: nuovo } : x))  // ottimistico
    const { error } = await supabase.from('amministratori').update({ [campo]: nuovo }).eq('id', a.id)
    if (error) { setErr(error.message); carica() }
  }

  return (
    <div className="pw-stack" style={{ maxWidth: 860 }}>
      <div>
        <div className="pw-eyebrow">Permessi</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Amministratori</h1>
        <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>
          Chi chiama da questi numeri è riconosciuto come amministratore (promemoria via voce).
          Con l'email + le spunte, ricevono via mail i nuovi ticket della priorità scelta. Tempo reale.
        </div>
      </div>

      <div className="pw-card">
        <div className="pw-card-head"><h3>Aggiungi amministratore</h3></div>
        <div className="pw-card-body pw-row" style={{ gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="pw-field" style={{ flex: 1, minWidth: 140 }}><label>Nome</label>
            <input className="pw-input" value={nome} onChange={e => setNome(e.target.value)} /></div>
          <div className="pw-field" style={{ flex: 1, minWidth: 140 }}><label>Telefono *</label>
            <input className="pw-input" placeholder="+39…" value={telefono} onChange={e => setTelefono(e.target.value)} /></div>
          <div className="pw-field" style={{ flex: 1, minWidth: 160 }}><label>Email</label>
            <input className="pw-input" placeholder="per i ticket" value={email} onChange={e => setEmail(e.target.value)} /></div>
          <button className="pw-btn pw-btn-primary" disabled={busy} onClick={aggiungi}>Aggiungi</button>
        </div>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        {righe.length === 0 ? <div className="pw-empty">Nessun amministratore.</div> : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr>
                <th>Nome</th><th>Telefono</th><th>Email</th>
                <th colSpan={3} style={{ textAlign: 'center' }}>Inoltra ticket via email</th><th></th>
              </tr>
              <tr>
                <th></th><th></th><th></th>
                {PRIORITA.map(([, lab]) => <th key={lab} style={{ textAlign: 'center', fontWeight: 500, fontSize: 12 }}>{lab}</th>)}
                <th></th>
              </tr></thead>
              <tbody>
                {righe.map(a => (
                  <tr key={a.id} style={{ cursor: 'default' }}>
                    <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{a.nome || '—'}</td>
                    <td>{a.telefono}</td>
                    <td style={{ color: a.email ? 'var(--fg-2)' : 'var(--muted, #999)', fontSize: 13 }}>{a.email || '—'}</td>
                    {PRIORITA.map(([campo]) => (
                      <td key={campo} style={{ textAlign: 'center' }}>
                        <input type="checkbox" checked={!!a[campo]} disabled={!a.email}
                          title={a.email ? '' : 'Aggiungi prima un\'email'} onChange={() => toggleFlag(a, campo)} />
                      </td>
                    ))}
                    <td style={{ textAlign: 'right' }}>
                      <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => elimina(a.id)}>Elimina</button>
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
