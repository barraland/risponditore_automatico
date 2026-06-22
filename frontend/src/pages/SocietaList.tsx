import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { STATI_REL, TIPI, badgeStato, lower, nomeAgente } from '../lib/format'
import Modal from '../components/Modal'

export default function SocietaList() {
  const nav = useNavigate()
  const [righe, setRighe] = useState<any[]>([])
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [nuova, setNuova] = useState(false)

  async function carica() {
    setLoading(true)
    const { data, error } = await supabase
      .from('locali')
      .select('id, insegna, ragione_sociale, tipo, citta, stato_relazione, agenti(nome, cognome), contatti(count), ordini(count)')
      .order('created_at', { ascending: false })
    if (error) setErr(error.message)
    else setRighe(data || [])
    setLoading(false)
  }
  useEffect(() => { carica() }, [])

  const filtrate = righe.filter(r => {
    if (!q) return true
    return `${r.insegna} ${r.ragione_sociale || ''} ${r.citta || ''}`.toLowerCase().includes(q.toLowerCase())
  })

  return (
    <div className="pw-stack">
      <div className="pw-between">
        <div>
          <div className="pw-eyebrow">CRM HORECA</div>
          <h1 style={{ fontSize: 28, marginTop: 6 }}>Società</h1>
        </div>
        <div className="pw-row">
          <input className="pw-input" style={{ maxWidth: 260 }} placeholder="Cerca…" value={q} onChange={e => setQ(e.target.value)} />
          <button className="pw-btn pw-btn-primary" onClick={() => setNuova(true)}>+ Nuova società</button>
        </div>
      </div>

      <div className="pw-card">
        {loading ? <div className="pw-spinner">Caricamento…</div>
          : err ? <div className="pw-card-body"><div className="pw-error">{err}</div></div>
          : filtrate.length === 0 ? <div className="pw-empty">Nessuna società.</div>
          : (
          <div style={{ overflowX: 'auto' }}>
            <table className="pw-table">
              <thead><tr><th>Società</th><th>Tipo</th><th>Città</th><th>Agente</th><th>Contatti</th><th>Ordini</th><th>Stato</th></tr></thead>
              <tbody>
                {filtrate.map(r => (
                  <tr key={r.id} onClick={() => nav(`/societa/${r.id}`)}>
                    <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{r.insegna}</td>
                    <td style={{ textTransform: 'capitalize' }}>{lower(r.tipo)}</td>
                    <td>{r.citta || '—'}</td>
                    <td>{nomeAgente(r.agenti) || '—'}</td>
                    <td>{r.contatti?.[0]?.count ?? 0}</td>
                    <td>{r.ordini?.[0]?.count ?? 0}</td>
                    <td><span className={`pw-badge ${badgeStato(r.stato_relazione)}`}>{lower(r.stato_relazione)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {nuova && <NuovaSocieta onClose={() => setNuova(false)} onCreata={(id) => nav(`/societa/${id}`)} />}
    </div>
  )
}

function NuovaSocieta({ onClose, onCreata }: { onClose: () => void; onCreata: (id: number) => void }) {
  const [f, setF] = useState({ insegna: '', ragione_sociale: '', tipo: 'RISTORANTE', citta: '', stato_relazione: 'PROSPECT' })
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: string, v: string) => setF({ ...f, [k]: v })

  async function salva() {
    if (!f.insegna.trim()) { setErr('Insegna obbligatoria.'); return }
    setBusy(true); setErr(null)
    const { data, error } = await supabase.from('locali').insert({
      insegna: f.insegna.trim(), ragione_sociale: f.ragione_sociale.trim() || null,
      tipo: f.tipo, citta: f.citta.trim() || null, stato_relazione: f.stato_relazione,
    }).select('id').single()
    setBusy(false)
    if (error) setErr(error.message)
    else onCreata(data!.id)
  }

  return (
    <Modal title="Nuova società" onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button>
               <button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{busy ? 'Salvo…' : 'Crea'}</button></>}>
      <div className="pw-field"><label>Insegna *</label><input className="pw-input" value={f.insegna} onChange={e => set('insegna', e.target.value)} /></div>
      <div className="pw-field"><label>Ragione sociale</label><input className="pw-input" value={f.ragione_sociale} onChange={e => set('ragione_sociale', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Tipo</label>
          <select className="pw-select" value={f.tipo} onChange={e => set('tipo', e.target.value)}>{TIPI.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Stato</label>
          <select className="pw-select" value={f.stato_relazione} onChange={e => set('stato_relazione', e.target.value)}>{STATI_REL.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
      </div>
      <div className="pw-field"><label>Città</label><input className="pw-input" value={f.citta} onChange={e => set('citta', e.target.value)} /></div>
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}
