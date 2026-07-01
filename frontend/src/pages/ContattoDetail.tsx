import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useAuth } from '../lib/auth'
import { badgePriorita, badgeStato, badgeTicket, dataBreve, dataOra, lower, nomeContatto } from '../lib/format'
import { useTenant } from '../lib/tenant'
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
  const { aziendaId } = useTenant()

  async function carica() {
    const { data, error } = await supabase.from('contatti').select(
      '*, locali(id, insegna, stato_relazione),' +
      ' messaggi_chat(id, direzione, testo, timestamp),' +
      ' chiamate_voce(id, iniziata_at, durata_sec, riassunto, trascrizione),' +
      ' ticket(id, titolo, stato, priorita, canale, created_at)'
    ).eq('id', id).single()
    if (error) setErr(error.message); else setC(data); setLoading(false)
  }
  useEffect(() => {
    supabase.from('locali').select('id, insegna').eq('azienda_id', aziendaId).order('insegna').then(({ data }) => setLocali(data || []))
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
          <h1 style={{ fontSize: 26 }}>{nomeContatto(c)}{c.locali && <span className={`pw-badge ${badgeStato(c.locali.stato_relazione)}`} style={{ verticalAlign: 'middle', marginLeft: 8 }}>{lower(c.locali.stato_relazione)}</span>}</h1>
          <div className="pw-muted" style={{ marginTop: 4 }}>
            {c.locali ? <Link to={`/societa/${c.locali.id}`}>{c.locali.insegna}</Link> : 'Nessuna società'}{c.ruolo ? ` · ${c.ruolo}` : ''}
          </div>
        </div>
        <div className="pw-row">
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setEdit(true)}>Modifica</button>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={elimina}>Elimina</button>
        </div>
      </div>

      <div className="pw-grid" style={{ gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.4fr)' }}>
        <div className="pw-stack">
          <div className="pw-card">
            <div className="pw-card-head"><h3>Anagrafica</h3></div>
            <div className="pw-card-body pw-stack" style={{ gap: 12, fontSize: 14 }}>
              <Kv k="Email" v={c.email} /><Kv k="Telefono" v={c.telefono} />
              <Kv k="Ruolo" v={c.ruolo} />
              <div>
                <div className="pw-muted" style={{ fontSize: 12 }}>Società</div>
                <div>{c.locali ? <Link to={`/societa/${c.locali.id}`}>{c.locali.insegna}</Link> : '—'}</div>
              </div>
            </div>
          </div>
          <Promemoria contattoId={c.id} />
        </div>
        <Conversazioni c={c} />
      </div>

      {edit && <EditContatto c={c} locali={locali} onClose={() => setEdit(false)} onSalvato={() => { setEdit(false); carica() }} />}
    </div>
  )
}

function Conversazioni({ c }: { c: any }) {
  const messaggi = (c.messaggi_chat || []).slice().sort((a: any, b: any) => (a.timestamp || '').localeCompare(b.timestamp || ''))
  const chiamate = (c.chiamate_voce || []).slice().sort((a: any, b: any) => (b.iniziata_at || '').localeCompare(a.iniziata_at || ''))
  const ticket = (c.ticket || []).slice().sort((a: any, b: any) => (b.created_at || '').localeCompare(a.created_at || ''))
  const vuoto = !messaggi.length && !chiamate.length && !ticket.length

  return (
    <div className="pw-stack">
      {vuoto && <div className="pw-card"><div className="pw-empty">Nessuna conversazione, chiamata o ticket.</div></div>}

      {ticket.length > 0 && (
        <div className="pw-card">
          <div className="pw-card-head"><h3>Ticket ({ticket.length})</h3></div>
          <div className="pw-card-body pw-stack" style={{ gap: 8 }}>
            {ticket.map((t: any) => (
              <div key={t.id} className="pw-between" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
                <div>
                  <div style={{ color: 'var(--fg)', fontWeight: 600 }}>{t.titolo}</div>
                  <div className="pw-muted" style={{ fontSize: 12 }}>{lower(t.canale) || '—'} · {dataOra(t.created_at)}</div>
                </div>
                <div className="pw-row" style={{ gap: 6 }}>
                  {t.priorita && <span className={`pw-badge ${badgePriorita(t.priorita)}`}>{lower(t.priorita)}</span>}
                  <span className={`pw-badge ${badgeTicket(t.stato)}`}>{lower(t.stato)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {chiamate.length > 0 && (
        <div className="pw-card">
          <div className="pw-card-head"><h3>Chiamate ({chiamate.length})</h3></div>
          <div className="pw-card-body pw-stack" style={{ gap: 12 }}>
            {chiamate.map((ch: any) => (
              <div key={ch.id} style={{ borderBottom: '1px solid var(--border)', paddingBottom: 10 }}>
                <div className="pw-muted" style={{ fontSize: 12 }}>
                  {dataOra(ch.iniziata_at)}{ch.durata_sec ? ` · ${Math.round(ch.durata_sec / 60)} min` : ''}
                </div>
                <div style={{ color: 'var(--fg-2)', fontSize: 14, marginTop: 4 }}>{ch.riassunto || '—'}</div>
                {ch.trascrizione && (
                  <details style={{ marginTop: 6 }}>
                    <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--acc-cy, #6EE7FF)' }}>Trascrizione</summary>
                    <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12.5, color: 'var(--fg-2)', marginTop: 8, maxHeight: '30vh', overflow: 'auto' }}>{ch.trascrizione}</pre>
                  </details>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {messaggi.length > 0 && (
        <div className="pw-card">
          <div className="pw-card-head"><h3>WhatsApp ({messaggi.length})</h3></div>
          <div className="pw-card-body pw-stack" style={{ gap: 8, maxHeight: '50vh', overflow: 'auto' }}>
            {messaggi.map((m: any) => {
              const out = lower(m.direzione) === 'out'
              return (
                <div key={m.id} style={{ display: 'flex', justifyContent: out ? 'flex-end' : 'flex-start' }}>
                  <div style={{
                    maxWidth: '80%', padding: '8px 12px', borderRadius: 12, fontSize: 13.5,
                    background: out ? 'var(--acc-grad-soft)' : 'rgba(255,255,255,.04)',
                    border: '1px solid var(--border)', color: 'var(--fg-2)',
                  }}>
                    <div>{m.testo}</div>
                    <div className="pw-muted" style={{ fontSize: 10.5, marginTop: 3, textAlign: 'right' }}>{dataOra(m.timestamp)}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function Promemoria({ contattoId }: { contattoId: number }) {
  const [note, setNote] = useState<any[]>([])
  const [testo, setTesto] = useState('')
  const [giorni, setGiorni] = useState('')
  const [busy, setBusy] = useState(false)
  const { aziendaId } = useTenant()

  async function carica() {
    if (!aziendaId) return
    const { data } = await supabase.from('promemoria')
      .select('id, testo, scade_il, created_at').eq('contatto_id', contattoId)
      .eq('azienda_id', aziendaId)
      .order('created_at', { ascending: false })
    setNote(data || [])
  }
  useEffect(() => { carica() }, [contattoId])

  async function aggiungi() {
    if (!testo.trim()) return
    setBusy(true)
    const g = parseInt(giorni)
    const scade = g > 0 ? new Date(Date.now() + g * 86400000).toISOString() : null
    await supabase.from('promemoria').insert({ contatto_id: contattoId, testo: testo.trim(), scade_il: scade, azienda_id: aziendaId })
    setBusy(false); setTesto(''); setGiorni(''); carica()
  }
  async function elimina(id: number) {
    await supabase.from('promemoria').delete().eq('id', id); carica()
  }
  const scaduto = (s?: string) => (s ? new Date(s) < new Date() : false)

  return (
    <div className="pw-card">
      <div className="pw-card-head"><h3>Promemoria <span className="pw-muted" style={{ fontWeight: 400, fontSize: 13 }}>— l'assistente li usa quando il cliente chiama</span></h3></div>
      <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
        {note.length === 0 && <div className="pw-muted" style={{ fontSize: 13 }}>Nessun promemoria.</div>}
        {note.map(n => (
          <div key={n.id} className="pw-between" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 8, opacity: scaduto(n.scade_il) ? 0.5 : 1 }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ color: 'var(--fg-2)', fontSize: 14 }}>{n.testo}</div>
              <div className="pw-muted" style={{ fontSize: 12 }}>
                {n.scade_il ? `${scaduto(n.scade_il) ? 'scaduto il' : 'valido fino al'} ${dataBreve(n.scade_il)}` : 'senza scadenza'}
              </div>
            </div>
            <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => elimina(n.id)}>✕</button>
          </div>
        ))}
        <textarea className="pw-input" rows={2} style={{ resize: 'vertical', fontFamily: 'inherit' }}
          placeholder="Nuovo promemoria (es. sconto sulle birre scure)…" value={testo} onChange={e => setTesto(e.target.value)} />
        <div className="pw-row" style={{ gap: 8 }}>
          <input className="pw-input" style={{ maxWidth: 150 }} placeholder="validità (giorni)" value={giorni} onChange={e => setGiorni(e.target.value)} />
          <button className="pw-btn pw-btn-primary pw-btn-sm" disabled={busy} onClick={aggiungi}>Aggiungi</button>
        </div>
      </div>
    </div>
  )
}

function Kv({ k, v }: { k: string; v?: string | null }) {
  return <div><div className="pw-muted" style={{ fontSize: 12 }}>{k}</div><div style={{ color: 'var(--fg-2)' }}>{v || '—'}</div></div>
}

function EditContatto({ c, locali, onClose, onSalvato }: any) {
  const [f, setF] = useState({
    nome: c.nome || '', cognome: c.cognome || '', ruolo: c.ruolo || '', telefono: c.telefono || '',
    email: c.email || '', locale_id: c.locale_id ? String(c.locale_id) : '',
  })
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const set = (k: string, v: string) => setF({ ...f, [k]: v })
  async function salva() {
    setBusy(true); setErr(null)
    // lo stato cliente/prospect è ereditato dalla società (sola lettura): non si scrive qui.
    const { error } = await supabase.from('contatti').update({
      nome: f.nome.trim() || null, cognome: f.cognome.trim() || null, ruolo: f.ruolo.trim() || null,
      telefono: f.telefono.trim() || null, email: f.email.trim() || null,
      locale_id: f.locale_id ? Number(f.locale_id) : null,
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
