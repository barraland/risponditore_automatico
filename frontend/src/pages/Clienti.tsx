import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'

type Riga = {
  id: number
  nome: string
  telefono: string | null
  numeri_voce: string | null
  whatsapp_phone_id: string | null
}

export default function Clienti() {
  const { isSuperAdmin, ready, reload } = useTenant()
  const [righe, setRighe] = useState<Riga[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [nuovo, setNuovo] = useState({ nome: '', numeri_voce: '', whatsapp_phone_id: '' })

  async function carica() {
    setLoading(true)
    const { data, error } = await supabase
      .from('azienda')
      .select('id, nome, telefono, numeri_voce, whatsapp_phone_id')
      .order('id')
    if (error) setErr(error.message)
    else setRighe((data || []) as Riga[])
    setLoading(false)
  }
  useEffect(() => { carica() }, [])

  async function crea() {
    setErr(null)
    if (!nuovo.nome.trim()) { setErr('Il nome del cliente è obbligatorio.'); return }
    const { error } = await supabase.from('azienda').insert({
      nome: nuovo.nome.trim(),
      numeri_voce: nuovo.numeri_voce.trim() || null,
      whatsapp_phone_id: nuovo.whatsapp_phone_id.trim() || null,
    })
    if (error) { setErr(error.message); return }
    setNuovo({ nome: '', numeri_voce: '', whatsapp_phone_id: '' })
    await carica()
    await reload() // aggiorna il selettore in alto
  }

  async function salvaCampo(r: Riga, campo: 'numeri_voce' | 'whatsapp_phone_id', valore: string) {
    const { error } = await supabase.from('azienda').update({ [campo]: valore.trim() || null }).eq('id', r.id)
    if (error) setErr(error.message)
    else { await carica(); await reload() }
  }

  if (ready && !isSuperAdmin) {
    return <div className="pw-card">Accesso riservato al super-admin.</div>
  }

  return (
    <div className="pw-stack">
      <div>
        <div className="pw-eyebrow">Multi-tenant</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Clienti</h1>
        <p className="pw-muted" style={{ maxWidth: 640 }}>
          Ogni cliente è un <strong>tenant</strong> isolato. Il tenant delle telefonate è il
          <em> numero chiamato</em>: elenca qui i numeri di voce (uno per riga o separati da virgola,
          solo cifre) e il <em>Phone Number ID</em> di WhatsApp per instradare le conversazioni.
        </p>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        <h3 style={{ marginTop: 0 }}>Nuovo cliente</h3>
        <div className="pw-row" style={{ flexWrap: 'wrap', gap: 8 }}>
          <input className="pw-input" placeholder="Nome cliente" style={{ maxWidth: 240 }}
            value={nuovo.nome} onChange={e => setNuovo({ ...nuovo, nome: e.target.value })} />
          <input className="pw-input" placeholder="Numeri voce (es. +3902…, +3906…)" style={{ maxWidth: 260 }}
            value={nuovo.numeri_voce} onChange={e => setNuovo({ ...nuovo, numeri_voce: e.target.value })} />
          <input className="pw-input" placeholder="WhatsApp Phone Number ID" style={{ maxWidth: 220 }}
            value={nuovo.whatsapp_phone_id} onChange={e => setNuovo({ ...nuovo, whatsapp_phone_id: e.target.value })} />
          <button className="pw-btn pw-btn-primary" onClick={crea}>Crea cliente</button>
        </div>
      </div>

      {loading ? <div className="pw-spinner">Caricamento…</div> : (
        <table className="pw-table">
          <thead>
            <tr><th>ID</th><th>Nome</th><th>Numeri voce</th><th>WhatsApp Phone ID</th></tr>
          </thead>
          <tbody>
            {righe.map(r => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.nome}</td>
                <td>
                  <input className="pw-input pw-btn-sm" defaultValue={r.numeri_voce || ''}
                    onBlur={e => e.target.value !== (r.numeri_voce || '') && salvaCampo(r, 'numeri_voce', e.target.value)} />
                </td>
                <td>
                  <input className="pw-input pw-btn-sm" defaultValue={r.whatsapp_phone_id || ''}
                    onBlur={e => e.target.value !== (r.whatsapp_phone_id || '') && salvaCampo(r, 'whatsapp_phone_id', e.target.value)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
