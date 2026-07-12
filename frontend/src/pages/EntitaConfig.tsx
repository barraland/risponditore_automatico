import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'

type Campo = { chiave: string; label: string; tipo: string; obbligatorio: boolean; opzioni: string[] }
type Tipo = {
  id?: number; nome_singolare: string; nome_plurale: string; max_per_contatto: number
  condivisibile: boolean; campo_etichetta: string; campi: Campo[]; attivo: boolean
}

const TIPI: [string, string][] = [
  ['testo', 'Testo'], ['numero', 'Numero'], ['data', 'Data'], ['scelta', 'Scelta (lista)'],
]

const slug = (s: string) =>
  (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'campo'

// Input opzioni: tiene il TESTO grezzo in stato locale (così la virgola si può digitare), e
// riporta al parent l'array parsato. Il valore mostrato non viene ri-derivato dall'array a ogni tasto.
function OpzioniInput({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [txt, setTxt] = useState((value || []).join(', '))
  // Consenti SOLO lettere (anche accentate), numeri, spazio e virgola: scarta altra punteggiatura.
  const pulisci = (s: string) => s.replace(/[^\p{L}\p{N} ,]/gu, '')
  return (
    <input className="pw-input" value={txt} placeholder="Es. cane, gatto, coniglio"
      onChange={e => { const v = pulisci(e.target.value); setTxt(v); onChange(v.split(',').map(s => s.trim()).filter(Boolean)) }} />
  )
}

const vuoto = (): Tipo => ({
  nome_singolare: '', nome_plurale: '', max_per_contatto: 0, condivisibile: true,
  campo_etichetta: '', campi: [], attivo: true,
})

const templateSocieta = (): Tipo => ({
  nome_singolare: 'Società', nome_plurale: 'Società', max_per_contatto: 1, condivisibile: true,
  campo_etichetta: 'insegna', attivo: true,
  campi: [
    { chiave: 'insegna', label: 'Insegna', tipo: 'testo', obbligatorio: true, opzioni: [] },
    { chiave: 'ragione_sociale', label: 'Ragione sociale', tipo: 'testo', obbligatorio: false, opzioni: [] },
    { chiave: 'citta', label: 'Città', tipo: 'testo', obbligatorio: false, opzioni: [] },
    { chiave: 'piva', label: 'Partita IVA', tipo: 'testo', obbligatorio: false, opzioni: [] },
  ],
})

const CONTATTO_CAMPI: [string, string][] = [
  ['nome', 'Nome'], ['cognome', 'Cognome'], ['telefono', 'Telefono'], ['email', 'Email'], ['ruolo', 'Ruolo'],
]
const CONTATTO_DEFAULT = ['nome', 'telefono']

// Config: quali dati della PERSONA (contatto) l'assistente chiede SEMPRE. Salva su azienda.contatto_obbligatori.
function ContattoObbligatori() {
  const { aziendaId } = useTenant()
  const [obbl, setObbl] = useState<string[] | null>(null)

  useEffect(() => {
    if (!aziendaId) return
    supabase.from('azienda').select('contatto_obbligatori').eq('id', aziendaId).maybeSingle()
      .then(({ data }) => {
        let v = CONTATTO_DEFAULT
        try { if (data?.contatto_obbligatori) { const p = JSON.parse(data.contatto_obbligatori); if (Array.isArray(p)) v = p } } catch { /* default */ }
        setObbl(v)
      })
  }, [aziendaId])

  async function toggle(k: string) {
    if (!obbl || !aziendaId) return
    const nv = obbl.includes(k) ? obbl.filter(x => x !== k) : [...obbl, k]
    setObbl(nv)
    await supabase.from('azienda').update({ contatto_obbligatori: JSON.stringify(nv) }).eq('id', aziendaId)
  }

  if (!obbl) return null
  return (
    <div className="pw-card">
      <div className="pw-card-head"><h3>Dati del contatto (persona)</h3></div>
      <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
        <div className="pw-muted" style={{ fontSize: 13 }}>
          Quali dati della persona l'assistente chiede <strong>sempre</strong> (obbligatori). Gli altri li
          raccoglie solo se emergono. L'elenco finisce automaticamente nel prompt — non va scritto a mano.
        </div>
        <div className="pw-row" style={{ gap: 16, flexWrap: 'wrap' }}>
          {CONTATTO_CAMPI.map(([k, l]) => (
            <label key={k} className="pw-row" style={{ gap: 6, cursor: 'pointer' }}>
              <input type="checkbox" checked={obbl.includes(k)} onChange={() => toggle(k)} /> {l}
            </label>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function EntitaConfig() {
  const { aziendaId } = useTenant()
  const [tipi, setTipi] = useState<Tipo[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [editing, setEditing] = useState<Tipo | null>(null)

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    setLoading(true); setErr(null)
    const { data, error } = await supabase.from('entita_tipo')
      .select('*').eq('azienda_id', aziendaId).order('id', { ascending: true })
    if (error) { setErr(error.message); setLoading(false); return }
    setTipi((data || []).map((t: any) => ({
      id: t.id, nome_singolare: t.nome_singolare || '', nome_plurale: t.nome_plurale || '',
      max_per_contatto: t.max_per_contatto ?? 0, condivisibile: t.condivisibile !== false,
      campo_etichetta: t.campo_etichetta || '', attivo: !!t.attivo,
      campi: (() => { try { return JSON.parse(t.campi || '[]') } catch { return [] } })(),
    })))
    setLoading(false)
  }
  useEffect(() => { carica() }, [aziendaId])

  async function attiva(t: Tipo) {
    if (!aziendaId || !t.id) return
    await supabase.from('entita_tipo').update({ attivo: false }).eq('azienda_id', aziendaId)
    await supabase.from('entita_tipo').update({ attivo: true }).eq('id', t.id)
    carica()
  }
  async function elimina(t: Tipo) {
    if (!t.id) return
    if (!confirm(`Eliminare il tipo «${t.nome_singolare}»? Verranno rimosse anche le entità di questo tipo già registrate.`)) return
    const { error } = await supabase.from('entita_tipo').delete().eq('id', t.id)
    if (error) setErr(error.message); else carica()
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>

  if (editing) {
    return <Editor tipo={editing} aziendaId={aziendaId!} onClose={() => setEditing(null)}
      onSaved={() => { setEditing(null); carica() }} />
  }

  return (
    <div className="pw-stack" style={{ maxWidth: 860 }}>
      <div className="pw-between" style={{ flexWrap: 'wrap', gap: 10 }}>
        <div>
          <div className="pw-eyebrow">CRM · configurazione admin</div>
          <h1 style={{ fontSize: 28, marginTop: 6 }}>Entità</h1>
          <div className="pw-muted" style={{ marginTop: 6, fontSize: 14, maxWidth: 640 }}>
            Cosa si lega a un contatto in questo contesto: <em>società</em> (horeca), <em>animale</em> (vet),
            <em> deceduto</em> (onoranze)… Definisci i tipi e i campi. L'assistente usa il tipo
            <strong> Attivo</strong> per chiedere e registrare i dati.
          </div>
        </div>
        <div className="pw-row" style={{ gap: 8 }}>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setEditing(templateSocieta())}>+ Società (predefinito)</button>
          <button className="pw-btn pw-btn-primary pw-btn-sm" onClick={() => setEditing(vuoto())}>+ Nuovo tipo</button>
        </div>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <ContattoObbligatori />

      <div className="pw-card-head" style={{ border: 'none', paddingLeft: 0 }}><h3>Tipi di entità</h3></div>
      <div className="pw-card">
        {tipi.length === 0
          ? <div className="pw-empty">Nessun tipo di entità. Aggiungine uno (es. «Società» o «Animale»).</div>
          : (
            <div style={{ overflowX: 'auto' }}>
              <table className="pw-table">
                <thead><tr><th>Tipo</th><th>Campi</th><th>Per contatto</th><th>Attivo</th><th></th></tr></thead>
                <tbody>
                  {tipi.map(t => (
                    <tr key={t.id}>
                      <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{t.nome_singolare}
                        {t.nome_plurale ? <span className="pw-muted" style={{ fontWeight: 400 }}> / {t.nome_plurale}</span> : null}</td>
                      <td>{t.campi.length} {t.campi.some(c => c.obbligatorio) ? `(${t.campi.filter(c => c.obbligatorio).length} obbl.)` : ''}</td>
                      <td>{t.max_per_contatto === 1 ? 'una sola' : 'più di una'}</td>
                      <td>
                        {t.attivo
                          ? <span className="pw-badge ok">Attivo</span>
                          : <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => attiva(t)}>Attiva</button>}
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setEditing(t)}>Modifica</button>{' '}
                        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => elimina(t)}>Elimina</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
      <div className="pw-muted" style={{ fontSize: 12 }}>
        Un solo tipo <strong>Attivo</strong> alla volta (è quello che l'assistente chiede/registra). Attivarne uno disattiva gli altri.
      </div>
    </div>
  )
}

function Editor({ tipo, aziendaId, onClose, onSaved }: {
  tipo: Tipo; aziendaId: number; onClose: () => void; onSaved: () => void
}) {
  const [nomeSing, setNomeSing] = useState(tipo.nome_singolare)
  const [nomePlur, setNomePlur] = useState(tipo.nome_plurale)
  const [maxPer, setMaxPer] = useState(tipo.max_per_contatto)
  const [condiv, setCondiv] = useState(tipo.condivisibile)
  const [campoEtichetta, setCampoEtichetta] = useState(tipo.campo_etichetta)
  const [campi, setCampi] = useState<Campo[]>(tipo.campi)
  const [err, setErr] = useState<string | null>(null)

  function patchCampo(i: number, p: Partial<Campo>) { setCampi(cs => cs.map((c, j) => j === i ? { ...c, ...p } : c)) }
  function addCampo() { setCampi(cs => [...cs, { chiave: '', label: '', tipo: 'testo', obbligatorio: false, opzioni: [] }]) }
  function delCampo(i: number) { setCampi(cs => cs.filter((_, j) => j !== i)) }

  async function salva() {
    if (!nomeSing.trim()) { setErr('Dai un nome all\'entità (singolare).'); return }
    setErr(null)
    const usate = new Set<string>()
    const campiNorm = campi.filter(c => (c.label || '').trim()).map(c => {
      let k = (c.chiave || slug(c.label)).trim() || slug(c.label)
      while (usate.has(k)) k = k + '_2'
      usate.add(k)
      return {
        chiave: k, label: c.label.trim(), tipo: c.tipo, obbligatorio: !!c.obbligatorio,
        opzioni: c.tipo === 'scelta' ? (c.opzioni || []).filter(Boolean) : [],
      }
    })
    const etich = campiNorm.find(c => c.chiave === campoEtichetta) ? campoEtichetta : (campiNorm[0]?.chiave || '')
    const payload = {
      azienda_id: aziendaId, nome_singolare: nomeSing.trim(), nome_plurale: nomePlur.trim() || null,
      max_per_contatto: maxPer, condivisibile: condiv, campo_etichetta: etich || null,
      campi: JSON.stringify(campiNorm), attivo: tipo.attivo,
    }
    const res = tipo.id
      ? await supabase.from('entita_tipo').update(payload).eq('id', tipo.id)
      : await supabase.from('entita_tipo').insert(payload).select('id').single()
    if (res.error) { setErr(res.error.message); return }
    // se questo tipo è attivo, disattiva gli altri (uno solo attivo per tenant)
    if (payload.attivo) {
      const idNew = tipo.id || (res as any).data?.id
      if (idNew) await supabase.from('entita_tipo').update({ attivo: false }).eq('azienda_id', aziendaId).neq('id', idNew)
    }
    onSaved()
  }

  return (
    <div className="pw-stack" style={{ maxWidth: 860 }}>
      <div className="pw-between" style={{ flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div className="pw-eyebrow">CRM · configurazione admin</div>
          <h1 style={{ fontSize: 26, marginTop: 6 }}>{tipo.id ? 'Modifica tipo' : 'Nuovo tipo di entità'}</h1>
        </div>
        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={onClose}>‹ Torna alla lista</button>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        <div className="pw-card-head"><h3>Definizione</h3></div>
        <div className="pw-card-body pw-stack" style={{ gap: 14 }}>
          <div className="pw-row" style={{ gap: 12, flexWrap: 'wrap' }}>
            <div className="pw-field" style={{ flex: 1, minWidth: 200 }}>
              <label>Nome (singolare)</label>
              <input className="pw-input" value={nomeSing} placeholder="Es. Animale" onChange={e => setNomeSing(e.target.value)} />
            </div>
            <div className="pw-field" style={{ flex: 1, minWidth: 200 }}>
              <label>Nome (plurale)</label>
              <input className="pw-input" value={nomePlur} placeholder="Es. Animali" onChange={e => setNomePlur(e.target.value)} />
            </div>
          </div>
          <div className="pw-row" style={{ gap: 20, flexWrap: 'wrap', alignItems: 'center' }}>
            <div className="pw-field" style={{ minWidth: 260 }}>
              <label>Quante per contatto</label>
              <select className="pw-select" value={maxPer} onChange={e => setMaxPer(Number(e.target.value))}>
                <option value={1}>Una sola (es. 1 società)</option>
                <option value={0}>Più di una (es. N animali)</option>
              </select>
            </div>
            <label className="pw-row" style={{ gap: 8, cursor: 'pointer', marginTop: 18 }}
              title="Se attivo, la stessa entità può essere collegata a più contatti (es. locale con più referenti, o due padroni dello stesso animale)">
              <input type="checkbox" checked={condiv} onChange={e => setCondiv(e.target.checked)} />
              Condivisibile tra più contatti
            </label>
          </div>
        </div>
      </div>

      <div className="pw-card">
        <div className="pw-card-head pw-between" style={{ alignItems: 'center' }}>
          <h3>Campi</h3>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={addCampo}>+ Aggiungi campo</button>
        </div>
        <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
          {campi.length === 0 && <div className="pw-muted" style={{ fontSize: 13 }}>Nessun campo. Aggiungine almeno uno (es. «Specie»).</div>}
          {campi.map((c, i) => (
            <div key={i} className="pw-stack" style={{ gap: 8, border: '1px solid var(--border)', borderRadius: 10, padding: 12 }}>
              <div className="pw-row" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <div className="pw-field" style={{ flex: 2, minWidth: 180 }}>
                  <label>Etichetta del campo</label>
                  <input className="pw-input" value={c.label} placeholder="Es. Specie animale"
                    onChange={e => patchCampo(i, { label: e.target.value, chiave: slug(e.target.value) })} />
                </div>
                <div className="pw-field" style={{ minWidth: 150 }}>
                  <label>Tipo</label>
                  <select className="pw-select" value={c.tipo} onChange={e => patchCampo(i, { tipo: e.target.value })}>
                    {TIPI.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </div>
                <label className="pw-row" style={{ gap: 6, cursor: 'pointer', paddingBottom: 8, whiteSpace: 'nowrap' }}
                  title="Se attivo, l'assistente lo chiede SEMPRE in fase di registrazione">
                  <input type="checkbox" checked={c.obbligatorio} onChange={e => patchCampo(i, { obbligatorio: e.target.checked })} />
                  Sempre richiesto
                </label>
                <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => delCampo(i)} style={{ paddingBottom: 8 }}>Rimuovi</button>
              </div>
              {c.tipo === 'scelta' && (
                <div className="pw-field">
                  <label>Opzioni (separate da virgola)</label>
                  <OpzioniInput value={c.opzioni || []} onChange={v => patchCampo(i, { opzioni: v })} />
                </div>
              )}
              <div className="pw-muted" style={{ fontSize: 11 }}>chiave: <code>{c.chiave || slug(c.label)}</code></div>
            </div>
          ))}
          {campi.length > 0 && (
            <div className="pw-field" style={{ maxWidth: 340 }}>
              <label>Campo che fa da “nome” dell'entità</label>
              <select className="pw-select" value={campoEtichetta} onChange={e => setCampoEtichetta(e.target.value)}>
                <option value="">(automatico: primo campo valorizzato)</option>
                {campi.filter(c => c.label.trim()).map((c, i) => (
                  <option key={i} value={c.chiave || slug(c.label)}>{c.label}</option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>

      <div className="pw-row" style={{ gap: 10 }}>
        <button className="pw-btn pw-btn-primary" onClick={salva}>Salva</button>
        <button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button>
      </div>
    </div>
  )
}
