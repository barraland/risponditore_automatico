import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import {
  CANALI, STATI_ORDINE, STATI_REL, TIPI,
  badgeOrdine, badgeStato, dataBreve, euro, lower, nomeAgente, nomeContatto,
} from '../lib/format'
import Modal from '../components/Modal'

const totale = (o: any) => (o.righe_ordine || []).reduce((s: number, r: any) => s + (r.prezzo_unitario ? (r.quantita || 0) * r.prezzo_unitario : 0), 0)

export default function SocietaDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const [soc, setSoc] = useState<any>(null)
  const [agenti, setAgenti] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [modal, setModal] = useState<null | 'edit' | 'contatto'>(null)

  async function carica() {
    const { data, error } = await supabase
      .from('locali')
      .select('*, agenti(id, nome, cognome), contatti(id, nome, cognome, ruolo, telefono, is_primario), ordini(id, data, canale, origine, stato, contatto_id, agente_id, righe_ordine(quantita, prezzo_unitario))')
      .eq('id', id).single()
    if (error) setErr(error.message); else setSoc(data)
    setLoading(false)
  }
  useEffect(() => {
    supabase.from('agenti').select('id, nome, cognome').order('cognome').then(({ data }) => setAgenti(data || []))
    carica()
  }, [id])

  async function promuovi(stato: string) {
    await supabase.from('locali').update({ stato_relazione: stato }).eq('id', id)
    carica()
  }
  async function elimina() {
    if (!confirm('Eliminare la società? Gli ordini verranno rimossi, i contatti scollegati.')) return
    const ids = (soc.ordini || []).map((o: any) => o.id)
    if (ids.length) {
      await supabase.from('righe_ordine').delete().in('ordine_id', ids)
      await supabase.from('ordini').delete().eq('locale_id', id)
    }
    await supabase.from('contatti').update({ locale_id: null }).eq('locale_id', id)
    const { error } = await supabase.from('locali').delete().eq('id', id)
    if (error) { alert(error.message); return }
    nav('/societa')
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>
  if (err) return <div className="pw-error">{err}</div>
  if (!soc) return <div className="pw-empty">Società non trovata.</div>

  const ordini = (soc.ordini || []).sort((a: any, b: any) => (b.data || '').localeCompare(a.data || ''))
  const contatti = (soc.contatti || []).sort((a: any, b: any) => Number(b.is_primario) - Number(a.is_primario))

  return (
    <div className="pw-stack">
      <Link to="/societa" className="pw-btn pw-btn-ghost pw-btn-sm" style={{ width: 'fit-content' }}>← Società</Link>

      <div className="pw-between">
        <div>
          <h1 style={{ fontSize: 26 }}>{soc.insegna}{' '}
            <span className={`pw-badge ${badgeStato(soc.stato_relazione)}`} style={{ verticalAlign: 'middle' }}>{lower(soc.stato_relazione)}</span>
          </h1>
          <div className="pw-muted" style={{ textTransform: 'capitalize', marginTop: 4 }}>{lower(soc.tipo)}{soc.citta ? ` · ${soc.citta}` : ''}</div>
        </div>
        <div className="pw-row">
          {lower(soc.stato_relazione) !== 'cliente'
            ? <button className="pw-btn pw-btn-primary pw-btn-sm" onClick={() => promuovi('CLIENTE')}>Promuovi a cliente</button>
            : <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => promuovi('PROSPECT')}>↺ a prospect</button>}
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setModal('edit')}>Modifica</button>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={elimina}>Elimina</button>
        </div>
      </div>

      <div className="pw-grid" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.4fr)' }}>
        <div className="pw-stack">
          <div className="pw-card">
            <div className="pw-card-head"><h3>Anagrafica</h3></div>
            <div className="pw-card-body pw-stack" style={{ gap: 12, fontSize: 14 }}>
              <Kv k="Ragione sociale" v={soc.ragione_sociale} />
              <Kv k="P. IVA" v={soc.piva} />
              <Kv k="Indirizzo" v={soc.indirizzo} />
              <Kv k="Agente" v={nomeAgente(soc.agenti)} />
              {soc.note && <Kv k="Note" v={soc.note} />}
            </div>
          </div>

          <div className="pw-card">
            <div className="pw-card-head"><h3>Referenti ({contatti.length})</h3>
              <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setModal('contatto')}>+ Referente</button></div>
            <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
              {contatti.length === 0 && <div className="pw-muted">Nessun referente.</div>}
              {contatti.map((c: any) => (
                <div key={c.id} className="pw-between" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
                  <div>
                    <Link to={`/contatti/${c.id}`} style={{ color: 'var(--fg)', fontWeight: 600 }}>{nomeContatto(c)}</Link>
                    {c.is_primario ? <span className="pw-badge lime" style={{ marginLeft: 8 }}>principale</span> : null}
                    <div className="pw-muted" style={{ fontSize: 13 }}>{c.ruolo || '—'}{c.telefono ? ` · ${c.telefono}` : ''}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <NuovoOrdine soc={soc} agenti={agenti} onCreato={() => carica()} />
        </div>

        <div className="pw-card">
          <div className="pw-card-head"><h3>Ordini ({ordini.length})</h3></div>
          {ordini.length === 0 ? <div className="pw-empty">Nessun ordine.</div> : (
            <div style={{ overflowX: 'auto' }}>
              <table className="pw-table">
                <thead><tr><th>#</th><th>Data</th><th>Canale</th><th>Totale</th><th>Stato</th></tr></thead>
                <tbody>
                  {ordini.map((o: any) => (
                    <tr key={o.id} onClick={() => nav(`/ordini/${o.id}`)}>
                      <td>#{o.id}</td><td>{dataBreve(o.data)}</td>
                      <td><span className="pw-badge mute">{lower(o.canale)}</span></td>
                      <td>{euro(totale(o))}</td>
                      <td><span className={`pw-badge ${badgeOrdine(o.stato)}`}>{lower(o.stato)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {modal === 'edit' && <EditSocieta soc={soc} agenti={agenti} onClose={() => setModal(null)} onSalvato={() => { setModal(null); carica() }} />}
      {modal === 'contatto' && <NuovoContatto localeId={soc.id} onClose={() => setModal(null)} onSalvato={() => { setModal(null); carica() }} />}
    </div>
  )
}

function Kv({ k, v }: { k: string; v?: string | null }) {
  return <div><div className="pw-muted" style={{ fontSize: 12 }}>{k}</div><div style={{ color: 'var(--fg-2)' }}>{v || '—'}</div></div>
}

function NuovoOrdine({ soc, agenti, onCreato }: { soc: any; agenti: any[]; onCreato: () => void }) {
  const [contatto, setContatto] = useState('')
  const [agente, setAgente] = useState(soc.agente_referente_id ? String(soc.agente_referente_id) : '')
  const [stato, setStato] = useState('CONFERMATO')
  const [righeTxt, setRighe] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  function parseRighe(t: string) {
    return t.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
      const p = l.split('|').map(x => x.trim())
      return { descrizione: p[0], quantita: p[1] ? parseFloat(p[1].replace(',', '.')) : 1, unita: p[2] || null, prezzo_unitario: p[3] ? parseFloat(p[3].replace(',', '.')) : null }
    }).filter(r => r.descrizione)
  }

  async function salva() {
    const righe = parseRighe(righeTxt)
    if (!righe.length) { setErr('Inserisci almeno una riga.'); return }
    setBusy(true); setErr(null)
    const { data, error } = await supabase.from('ordini').insert({
      locale_id: soc.id,
      contatto_id: contatto ? Number(contatto) : null,
      agente_id: agente ? Number(agente) : null,
      origine: agente && !contatto ? 'AGENTE' : 'CLIENTE',
      canale: 'MANUALE', stato, data: new Date().toISOString(),
    }).select('id').single()
    if (error) { setBusy(false); setErr(error.message); return }
    const rows = righe.map(r => ({ ...r, ordine_id: data!.id }))
    await supabase.from('righe_ordine').insert(rows)
    setBusy(false); setRighe(''); onCreato()
  }

  return (
    <div className="pw-card">
      <div className="pw-card-head"><h3>Nuovo ordine</h3></div>
      <div className="pw-card-body pw-stack" style={{ gap: 12 }}>
        <div className="pw-row" style={{ gap: 12 }}>
          <div className="pw-field" style={{ flex: 1 }}><label>Referente</label>
            <select className="pw-select" value={contatto} onChange={e => setContatto(e.target.value)}>
              <option value="">—</option>
              {(soc.contatti || []).map((c: any) => <option key={c.id} value={c.id}>{nomeContatto(c)}</option>)}
            </select></div>
          <div className="pw-field" style={{ flex: 1 }}><label>Agente</label>
            <select className="pw-select" value={agente} onChange={e => setAgente(e.target.value)}>
              <option value="">—</option>
              {agenti.map(a => <option key={a.id} value={a.id}>{nomeAgente(a)}</option>)}
            </select></div>
        </div>
        <div className="pw-field"><label>Righe (una per riga: descrizione | qtà | unità | prezzo)</label>
          <textarea className="pw-input" rows={4} style={{ resize: 'vertical', fontFamily: 'inherit' }}
            placeholder={'Farina 00 | 10 | sacchi | 12.50\nOlio EVO | 6 | latte | 45'}
            value={righeTxt} onChange={e => setRighe(e.target.value)} /></div>
        <div className="pw-row" style={{ gap: 12 }}>
          <div className="pw-field" style={{ flex: 1 }}><label>Stato</label>
            <select className="pw-select" value={stato} onChange={e => setStato(e.target.value)}>{STATI_ORDINE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
          <div style={{ alignSelf: 'flex-end' }}><button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{busy ? 'Creo…' : 'Crea ordine'}</button></div>
        </div>
        {err && <div className="pw-error">{err}</div>}
      </div>
    </div>
  )
}

function EditSocieta({ soc, agenti, onClose, onSalvato }: any) {
  const [f, setF] = useState({
    insegna: soc.insegna || '', ragione_sociale: soc.ragione_sociale || '', tipo: soc.tipo,
    piva: soc.piva || '', indirizzo: soc.indirizzo || '', citta: soc.citta || '',
    stato_relazione: soc.stato_relazione, agente_referente_id: soc.agente_referente_id ? String(soc.agente_referente_id) : '', note: soc.note || '',
  })
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  async function salva() {
    setBusy(true); setErr(null)
    const { error } = await supabase.from('locali').update({
      insegna: f.insegna.trim() || soc.insegna, ragione_sociale: f.ragione_sociale.trim() || null, tipo: f.tipo,
      piva: f.piva.trim() || null, indirizzo: f.indirizzo.trim() || null, citta: f.citta.trim() || null,
      stato_relazione: f.stato_relazione, agente_referente_id: f.agente_referente_id ? Number(f.agente_referente_id) : null, note: f.note.trim() || null,
    }).eq('id', soc.id)
    setBusy(false); if (error) setErr(error.message); else onSalvato()
  }
  return (
    <Modal title="Modifica società" onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button><button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>Salva</button></>}>
      <div className="pw-field"><label>Insegna</label><input className="pw-input" value={f.insegna} onChange={e => set('insegna', e.target.value)} /></div>
      <div className="pw-field"><label>Ragione sociale</label><input className="pw-input" value={f.ragione_sociale} onChange={e => set('ragione_sociale', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Tipo</label><select className="pw-select" value={f.tipo} onChange={e => set('tipo', e.target.value)}>{TIPI.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
        <div className="pw-field" style={{ flex: 1 }}><label>P. IVA</label><input className="pw-input" value={f.piva} onChange={e => set('piva', e.target.value)} /></div>
      </div>
      <div className="pw-field"><label>Indirizzo</label><input className="pw-input" value={f.indirizzo} onChange={e => set('indirizzo', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Città</label><input className="pw-input" value={f.citta} onChange={e => set('citta', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Stato</label><select className="pw-select" value={f.stato_relazione} onChange={e => set('stato_relazione', e.target.value)}>{STATI_REL.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
      </div>
      <div className="pw-field"><label>Agente di riferimento</label>
        <select className="pw-select" value={f.agente_referente_id} onChange={e => set('agente_referente_id', e.target.value)}>
          <option value="">—</option>{agenti.map((a: any) => <option key={a.id} value={a.id}>{nomeAgente(a)}</option>)}</select></div>
      <div className="pw-field"><label>Note</label><textarea className="pw-input" rows={2} style={{ resize: 'vertical', fontFamily: 'inherit' }} value={f.note} onChange={e => set('note', e.target.value)} /></div>
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}

function NuovoContatto({ localeId, onClose, onSalvato }: { localeId: number; onClose: () => void; onSalvato: () => void }) {
  const [f, setF] = useState({ nome: '', cognome: '', ruolo: '', telefono: '', email: '', is_primario: false })
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  async function salva() {
    setBusy(true); setErr(null)
    const { error } = await supabase.from('contatti').insert({
      nome: f.nome.trim() || null, cognome: f.cognome.trim() || null, ruolo: f.ruolo.trim() || null,
      telefono: f.telefono.trim() || null, email: f.email.trim() || null,
      locale_id: localeId, is_primario: f.is_primario, stato: 'PROSPECT',
    })
    setBusy(false); if (error) setErr(error.message); else onSalvato()
  }
  return (
    <Modal title="Nuovo referente" onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button><button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>Aggiungi</button></>}>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Nome</label><input className="pw-input" value={f.nome} onChange={e => set('nome', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Cognome</label><input className="pw-input" value={f.cognome} onChange={e => set('cognome', e.target.value)} /></div>
      </div>
      <div className="pw-field"><label>Ruolo</label><input className="pw-input" placeholder="Titolare, Chef, Acquisti…" value={f.ruolo} onChange={e => set('ruolo', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>
        <div className="pw-field" style={{ flex: 1 }}><label>Telefono</label><input className="pw-input" value={f.telefono} onChange={e => set('telefono', e.target.value)} /></div>
        <div className="pw-field" style={{ flex: 1 }}><label>Email</label><input className="pw-input" value={f.email} onChange={e => set('email', e.target.value)} /></div>
      </div>
      <label className="pw-row" style={{ gap: 8, fontSize: 14 }}><input type="checkbox" checked={f.is_primario} onChange={e => set('is_primario', e.target.checked)} /> Referente principale</label>
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}
