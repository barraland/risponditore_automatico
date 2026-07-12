import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { lower, nomeContatto } from '../lib/format'
import { useTenant } from '../lib/tenant'
import Modal from '../components/Modal'

const entita = (c: any) => (c.contatto_entita || []).map((l: any) => l.entita).filter(Boolean)

export default function ContattiList() {
  const nav = useNavigate()
  const [righe, setRighe] = useState<any[]>([])
  const [entLabel, setEntLabel] = useState('Entità')
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [nuovo, setNuovo] = useState(false)
  const { aziendaId } = useTenant()

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    const { data, error } = await supabase.from('contatti')
      .select('id, nome, cognome, ruolo, telefono, email, stato, contatto_entita(entita(id, etichetta))')
      .eq('azienda_id', aziendaId)
      .order('created_at', { ascending: false })
    if (error) setErr(error.message); else setRighe(data || []); setLoading(false)
  }
  useEffect(() => {
    supabase.from('entita_tipo').select('nome_plurale, nome_singolare').eq('azienda_id', aziendaId)
      .eq('attivo', true).order('id', { ascending: false }).limit(1)
      .then(({ data }) => { const t = (data || [])[0] as any; if (t) setEntLabel(t.nome_plurale || t.nome_singolare) })
    carica()
  }, [])

  const filtrate = righe.filter(r => {
    if (!q) return true
    const et = entita(r).map((e: any) => e.etichetta).join(' ')
    return `${nomeContatto(r)} ${r.telefono || ''} ${r.email || ''} ${et}`.toLowerCase().includes(q.toLowerCase())
  })

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div><div className="pw-eyebrow">CRM</div><h1 style={{ fontSize: 28, marginTop: 6 }}>Contatti</h1></div>
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
              <thead><tr><th>Nome</th><th>{entLabel}</th><th>Ruolo</th><th>Telefono</th><th>Email</th><th>Stato</th></tr></thead>
              <tbody>
                {filtrate.map(c => {
                  const ents = entita(c)
                  return (
                    <tr key={c.id} onClick={() => nav(`/contatti/${c.id}`)}>
                      <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{nomeContatto(c)}</td>
                      <td>{ents.length
                        ? ents.map((e: any, i: number) => (
                          <span key={e.id}>{i > 0 ? ', ' : ''}
                            <Link to={`/entita-lista?open=${e.id}`} onClick={ev => ev.stopPropagation()}>{e.etichetta || '—'}</Link>
                          </span>))
                        : '—'}</td>
                      <td>{c.ruolo || '—'}</td><td>{c.telefono || '—'}</td><td>{c.email || '—'}</td>
                      <td><span className="pw-badge mute">{lower(c.stato) || '—'}</span></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {nuovo && <NuovoContatto onClose={() => setNuovo(false)} onCreato={(id) => nav(`/contatti/${id}`)} />}
    </div>
  )
}

// Normalizza un recapito come il backend (telefono: ultime 10 cifre; email: minuscolo/trim).
function normRec(tipo: string, val: string): string {
  const v = (val || '').trim()
  if (tipo === 'EMAIL') return v.toLowerCase()
  let d = v.replace(/\D/g, '')
  if (d.startsWith('00')) d = d.slice(2)
  return d.length >= 10 ? d.slice(-10) : d
}

function NuovoContatto({ onClose, onCreato }: { onClose: () => void; onCreato: (id: number) => void }) {
  const [f, setF] = useState({ nome: '', cognome: '', ruolo: '', telefono: '', email: '', stato: 'PROSPECT' })
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const { aziendaId } = useTenant()
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  async function salva() {
    setBusy(true); setErr(null)
    const { data, error } = await supabase.from('contatti').insert({
      nome: f.nome.trim() || null, cognome: f.cognome.trim() || null, ruolo: f.ruolo.trim() || null,
      telefono: f.telefono.trim() || null, email: f.email.trim() || null, stato: f.stato,
      azienda_id: aziendaId,
    }).select('id').single()
    if (error) { setBusy(false); setErr(error.message); return }
    const cid = data!.id
    // Recapiti principali (best-effort: se la tabella non esiste ancora, i valori restano sulle colonne).
    const recs: any[] = []
    const tel = f.telefono.trim(), em = f.email.trim()
    if (tel) recs.push({ azienda_id: aziendaId, contatto_id: cid, tipo: 'TELEFONO', valore: tel, valore_norm: normRec('TELEFONO', tel), principale: true })
    if (em) recs.push({ azienda_id: aziendaId, contatto_id: cid, tipo: 'EMAIL', valore: em, valore_norm: normRec('EMAIL', em), principale: true })
    if (recs.length) await supabase.from('recapito').insert(recs)
    setBusy(false); onCreato(cid)
  }
  return (
    <Modal title="Nuovo contatto" onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button><button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>Crea</button></>}>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Nome</label><input className="pw-input" value={f.nome} onChange={e => set('nome', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Cognome</label><input className="pw-input" value={f.cognome} onChange={e => set('cognome', e.target.value)} /></div>
      </div>
      <div className="pw-field"><label>Ruolo</label><input className="pw-input" placeholder="Titolare, Chef…" value={f.ruolo} onChange={e => set('ruolo', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Telefono</label><input className="pw-input" value={f.telefono} onChange={e => set('telefono', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Email</label><input className="pw-input" value={f.email} onChange={e => set('email', e.target.value)} /></div>
      </div>
      <div className="pw-muted" style={{ fontSize: 12 }}>Le entità (società, animali…) le colleghi dalla scheda del contatto, dopo averlo creato.</div>
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}
