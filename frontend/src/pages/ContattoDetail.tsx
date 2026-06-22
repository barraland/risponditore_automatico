import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../lib/auth'
import { lower, nomeContatto } from '../lib/format'
import Modal from '../components/Modal'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

export default function ContattoDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const { session } = useAuth()
  const [c, setC] = useState<any>(null)
  const [locali, setLocali] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [edit, setEdit] = useState(false)

  async function carica() {
    const { data, error } = await supabase.from('contatti').select('*, locali(id, insegna)').eq('id', id).single()
    if (error) setErr(error.message); else setC(data); setLoading(false)
  }
  useEffect(() => {
    supabase.from('locali').select('id, insegna').order('insegna').then(({ data }) => setLocali(data || []))
    carica()
  }, [id])

  async function elimina() {
    if (!confirm('Eliminare questo contatto? La sua storia (messaggi, chiamate, ticket) verrà rimossa; gli ordini restano ma scollegati.')) return
    if (!API) { setErr('VITE_API_BASE non configurato: serve il backend per eliminare un contatto con storico.'); return }
    const res = await fetch(`${API}/api/contatti/${id}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${session?.access_token}` },
    })
    if (!res.ok) {
      const t = await res.text().catch(() => '')
      setErr(`Eliminazione fallita (${res.status}): ${t.slice(0, 200)}`)
      return
    }
    nav('/contatti')
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>
  if (err) return <div className="pw-error">{err}</div>
  if (!c) return <div className="pw-empty">Contatto non trovato.</div>

  return (
    <div className="pw-stack">
      <Link to="/contatti" className="pw-btn pw-btn-ghost pw-btn-sm" style={{ width: 'fit-content' }}>← Contatti</Link>
      <div className="pw-between">
        <div>
          <h1 style={{ fontSize: 26 }}>{nomeContatto(c)} <span className={`pw-badge ${lower(c.stato) === 'cliente' ? 'ok' : 'warn'}`} style={{ verticalAlign: 'middle' }}>{lower(c.stato)}</span></h1>
          <div className="pw-muted" style={{ marginTop: 4 }}>
            {c.locali ? <Link to={`/societa/${c.locali.id}`}>{c.locali.insegna}</Link> : 'Nessuna società'}{c.ruolo ? ` · ${c.ruolo}` : ''}
          </div>
        </div>
        <div className="pw-row">
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setEdit(true)}>Modifica</button>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={elimina}>Elimina</button>
        </div>
      </div>

      <div className="pw-card" style={{ maxWidth: 520 }}>
        <div className="pw-card-head"><h3>Anagrafica</h3></div>
        <div className="pw-card-body pw-stack" style={{ gap: 12, fontSize: 14 }}>
          <Kv k="Email" v={c.email} /><Kv k="Telefono" v={c.telefono} />
          <Kv k="Ruolo" v={c.ruolo} /><Kv k="Società" v={c.locali?.insegna} />
        </div>
      </div>

      {edit && <EditContatto c={c} locali={locali} onClose={() => setEdit(false)} onSalvato={() => { setEdit(false); carica() }} />}
    </div>
  )
}

function Kv({ k, v }: { k: string; v?: string | null }) {
  return <div><div className="pw-muted" style={{ fontSize: 12 }}>{k}</div><div style={{ color: 'var(--fg-2)' }}>{v || '—'}</div></div>
}

function EditContatto({ c, locali, onClose, onSalvato }: any) {
  const [f, setF] = useState({
    nome: c.nome || '', cognome: c.cognome || '', ruolo: c.ruolo || '', telefono: c.telefono || '',
    email: c.email || '', locale_id: c.locale_id ? String(c.locale_id) : '', stato: c.stato,
  })
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  async function salva() {
    setBusy(true); setErr(null)
    const { error } = await supabase.from('contatti').update({
      nome: f.nome.trim() || null, cognome: f.cognome.trim() || null, ruolo: f.ruolo.trim() || null,
      telefono: f.telefono.trim() || null, email: f.email.trim() || null,
      locale_id: f.locale_id ? Number(f.locale_id) : null, stato: f.stato,
    }).eq('id', c.id)
    setBusy(false); if (error) setErr(error.message); else onSalvato()
  }
  return (
    <Modal title="Modifica contatto" onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button><button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>Salva</button></>}>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Nome</label><input className="pw-input" value={f.nome} onChange={e => set('nome', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Cognome</label><input className="pw-input" value={f.cognome} onChange={e => set('cognome', e.target.value)} /></div>
      </div>
      <div className="pw-field"><label>Società</label>
        <select className="pw-select" value={f.locale_id} onChange={e => set('locale_id', e.target.value)}>
          <option value="">— nessuna —</option>{locali.map((l: any) => <option key={l.id} value={l.id}>{l.insegna}</option>)}</select></div>
      <div className="pw-field"><label>Ruolo</label><input className="pw-input" value={f.ruolo} onChange={e => set('ruolo', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Telefono</label><input className="pw-input" value={f.telefono} onChange={e => set('telefono', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Email</label><input className="pw-input" value={f.email} onChange={e => set('email', e.target.value)} /></div>
      </div>
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}
