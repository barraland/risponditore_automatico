import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { nomeAgente } from '../lib/format'
import Modal from '../components/Modal'

export default function AgentiList() {
  const nav = useNavigate()
  const [righe, setRighe] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [nuovo, setNuovo] = useState(false)

  async function carica() {
    const { data, error } = await supabase.from('agenti')
      .select('id, nome, cognome, zona, telefono, email, percentuale_provvigione, locali(count), ordini(count)')
      .order('cognome')
    if (error) setErr(error.message); else setRighe(data || []); setLoading(false)
  }
  useEffect(() => { carica() }, [])

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div><div className="pw-eyebrow">Rete vendita</div><h1 style={{ fontSize: 28, marginTop: 6 }}>Agenti</h1></div>
        <button className="pw-btn pw-btn-primary" onClick={() => setNuovo(true)}>+ Nuovo agente</button>
      </div>
      <div className="pw-card">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : err ? <div className="pw-card-body"><div className="pw-error">{err}</div></div>
          : righe.length === 0 ? <div className="pw-empty">Nessun agente.</div>
          : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Agente</th><th>Zona</th><th>Telefono</th><th>Email</th><th>Società</th><th>Provvigione</th></tr></thead>
              <tbody>
                {righe.map(a => (
                  <tr key={a.id} onClick={() => nav(`/agenti/${a.id}`)}>
                    <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{nomeAgente(a) || '—'}</td>
                    <td>{a.zona || '—'}</td><td>{a.telefono || '—'}</td><td>{a.email || '—'}</td>
                    <td>{a.locali?.[0]?.count ?? 0}</td>
                    <td>{a.percentuale_provvigione != null ? `${a.percentuale_provvigione}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {nuovo && <NuovoAgente onClose={() => setNuovo(false)} onCreato={(id) => nav(`/agenti/${id}`)} />}
    </div>
  )
}

function NuovoAgente({ onClose, onCreato }: { onClose: () => void; onCreato: (id: number) => void }) {
  const [f, setF] = useState({ nome: '', cognome: '', zona: '', telefono: '', email: '', percentuale_provvigione: '' })
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  async function salva() {
    setBusy(true); setErr(null)
    const prov = f.percentuale_provvigione.replace(',', '.').trim()
    const { data, error } = await supabase.from('agenti').insert({
      nome: f.nome.trim() || null, cognome: f.cognome.trim() || null, zona: f.zona.trim() || null,
      telefono: f.telefono.trim() || null, email: f.email.trim() || null,
      percentuale_provvigione: prov ? parseFloat(prov) : null,
    }).select('id').single()
    setBusy(false); if (error) setErr(error.message); else onCreato(data!.id)
  }
  return (
    <Modal title="Nuovo agente" onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button><button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>Crea</button></>}>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Nome</label><input className="pw-input" value={f.nome} onChange={e => set('nome', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Cognome</label><input className="pw-input" value={f.cognome} onChange={e => set('cognome', e.target.value)} /></div>
      </div>
      <div className="pw-field"><label>Zona</label><input className="pw-input" value={f.zona} onChange={e => set('zona', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Telefono</label><input className="pw-input" value={f.telefono} onChange={e => set('telefono', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Email</label><input className="pw-input" value={f.email} onChange={e => set('email', e.target.value)} /></div>
      </div>
      <div className="pw-field"><label>Provvigione %</label><input className="pw-input" placeholder="Es. 5" value={f.percentuale_provvigione} onChange={e => set('percentuale_provvigione', e.target.value)} /></div>
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}
