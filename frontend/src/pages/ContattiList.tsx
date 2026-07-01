import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { badgeStato, lower, nomeContatto } from '../lib/format'
import { useTenant } from '../lib/tenant'
import Modal from '../components/Modal'

export default function ContattiList() {
  const nav = useNavigate()
  const [righe, setRighe] = useState<any[]>([])
  const [locali, setLocali] = useState<any[]>([])
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [nuovo, setNuovo] = useState(false)
  const { aziendaId } = useTenant()

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    const { data, error } = await supabase.from('contatti')
      .select('id, nome, cognome, ruolo, telefono, email, locali(id, insegna, stato_relazione)')
      .eq('azienda_id', aziendaId)
      .order('created_at', { ascending: false })
    if (error) setErr(error.message); else setRighe(data || []); setLoading(false)
  }
  useEffect(() => {
    supabase.from('locali').select('id, insegna').eq('azienda_id', aziendaId).order('insegna').then(({ data }) => setLocali(data || []))
    carica()
  }, [])

  const filtrate = righe.filter(r => {
    if (!q) return true
    return `${nomeContatto(r)} ${r.telefono || ''} ${r.email || ''} ${r.locali?.insegna || ''}`.toLowerCase().includes(q.toLowerCase())
  })

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div><div className="pw-eyebrow">CRM HORECA</div><h1 style={{ fontSize: 28, marginTop: 6 }}>Contatti</h1></div>
        <div className="pw-row">
          <input className="pw-input" style={{ maxWidth: 260 }} placeholder="Cerca…" value={q} onChange={e => setQ(e.target.value)} />
          <button className="pw-btn pw-btn-primary" onClick={() => setNuovo(true)}>+ Nuovo contatto</button>
        </div>
      </div>
      <div className="pw-card">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : err ? <div className="pw-card-body"><div className="pw-error">{err}</div></div>
          : filtrate.length === 0 ? <div className="pw-empty">Nessun contatto.</div>
          : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Nome</th><th>Società</th><th>Ruolo</th><th>Telefono</th><th>Email</th><th>Stato</th></tr></thead>
              <tbody>
                {filtrate.map(c => (
                  <tr key={c.id} onClick={() => nav(`/contatti/${c.id}`)}>
                    <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{nomeContatto(c)}</td>
                    <td>{c.locali
                      ? <Link to={`/societa/${c.locali.id}`} onClick={e => e.stopPropagation()}>{c.locali.insegna}</Link>
                      : '—'}</td>
                    <td>{c.ruolo || '—'}</td><td>{c.telefono || '—'}</td><td>{c.email || '—'}</td>
                    <td>{c.locali
                      ? <span className={`pw-badge ${badgeStato(c.locali.stato_relazione)}`}>{lower(c.locali.stato_relazione)}</span>
                      : <span className="pw-muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {nuovo && <NuovoContatto locali={locali} onClose={() => setNuovo(false)} onCreato={(id) => nav(`/contatti/${id}`)} />}
    </div>
  )
}

function NuovoContatto({ locali, onClose, onCreato }: { locali: any[]; onClose: () => void; onCreato: (id: number) => void }) {
  const [f, setF] = useState({ nome: '', cognome: '', ruolo: '', telefono: '', email: '', locale_id: '', stato: 'PROSPECT' })
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const { aziendaId } = useTenant()
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  async function salva() {
    setBusy(true); setErr(null)
    const { data, error } = await supabase.from('contatti').insert({
      nome: f.nome.trim() || null, cognome: f.cognome.trim() || null, ruolo: f.ruolo.trim() || null,
      telefono: f.telefono.trim() || null, email: f.email.trim() || null,
      locale_id: f.locale_id ? Number(f.locale_id) : null, stato: f.stato,
      azienda_id: aziendaId,
    }).select('id').single()
    setBusy(false); if (error) setErr(error.message); else onCreato(data!.id)
  }
  return (
    <Modal title="Nuovo contatto" onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button><button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>Crea</button></>}>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Nome</label><input className="pw-input" value={f.nome} onChange={e => set('nome', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Cognome</label><input className="pw-input" value={f.cognome} onChange={e => set('cognome', e.target.value)} /></div>
      </div>
      <div className="pw-field"><label>Società</label>
        <select className="pw-select" value={f.locale_id} onChange={e => set('locale_id', e.target.value)}>
          <option value="">— nessuna —</option>{locali.map(l => <option key={l.id} value={l.id}>{l.insegna}</option>)}</select></div>
      <div className="pw-field"><label>Ruolo</label><input className="pw-input" placeholder="Titolare, Chef…" value={f.ruolo} onChange={e => set('ruolo', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Telefono</label><input className="pw-input" value={f.telefono} onChange={e => set('telefono', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Email</label><input className="pw-input" value={f.email} onChange={e => set('email', e.target.value)} /></div>
      </div>
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}
