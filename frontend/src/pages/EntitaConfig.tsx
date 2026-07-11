import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'

type Campo = { chiave: string; label: string; tipo: string; obbligatorio: boolean; opzioni: string[] }

const TIPI: [string, string][] = [
  ['testo', 'Testo'], ['numero', 'Numero'], ['data', 'Data'], ['scelta', 'Scelta (lista)'],
]

const slug = (s: string) =>
  (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'campo'

export default function EntitaConfig() {
  const { aziendaId } = useTenant()
  const [id, setId] = useState<number | null>(null)
  const [nomeSing, setNomeSing] = useState('')
  const [nomePlur, setNomePlur] = useState('')
  const [maxPer, setMaxPer] = useState(0)            // 0 = illimitato (N), 1 = una sola
  const [condiv, setCondiv] = useState(true)
  const [campoEtichetta, setCampoEtichetta] = useState('')
  const [campi, setCampi] = useState<Campo[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    setLoading(true); setErr(null)
    const { data, error } = await supabase.from('entita_tipo')
      .select('*').eq('azienda_id', aziendaId).eq('attivo', true)
      .order('id', { ascending: false }).limit(1)
    if (error) { setErr(error.message); setLoading(false); return }
    const t = (data || [])[0]
    if (t) {
      setId(t.id); setNomeSing(t.nome_singolare || ''); setNomePlur(t.nome_plurale || '')
      setMaxPer(t.max_per_contatto ?? 0); setCondiv(t.condivisibile !== false)
      setCampoEtichetta(t.campo_etichetta || '')
      try { setCampi(JSON.parse(t.campi || '[]')) } catch { setCampi([]) }
    }
    setLoading(false)
  }
  useEffect(() => { carica() }, [aziendaId])

  function patchCampo(i: number, p: Partial<Campo>) {
    setCampi(cs => cs.map((c, j) => j === i ? { ...c, ...p } : c))
  }
  function addCampo() {
    setCampi(cs => [...cs, { chiave: '', label: '', tipo: 'testo', obbligatorio: false, opzioni: [] }])
  }
  function delCampo(i: number) {
    setCampi(cs => cs.filter((_, j) => j !== i))
  }

  async function salva() {
    if (!aziendaId) return
    if (!nomeSing.trim()) { setErr('Dai un nome all\'entità (singolare).'); return }
    setErr(null); setSaved(false)
    // normalizza le chiavi (dal label se vuote) e garantisci univocità
    const usate = new Set<string>()
    const campiNorm = campi.filter(c => (c.label || '').trim()).map(c => {
      let k = (c.chiave || slug(c.label)).trim() || slug(c.label)
      while (usate.has(k)) k = k + '_2'
      usate.add(k)
      return { chiave: k, label: c.label.trim(), tipo: c.tipo,
               obbligatorio: !!c.obbligatorio,
               opzioni: c.tipo === 'scelta' ? (c.opzioni || []).filter(Boolean) : [] }
    })
    const etich = campiNorm.find(c => c.chiave === campoEtichetta) ? campoEtichetta : (campiNorm[0]?.chiave || '')
    const payload = {
      azienda_id: aziendaId, nome_singolare: nomeSing.trim(), nome_plurale: nomePlur.trim() || null,
      max_per_contatto: maxPer, condivisibile: condiv, campo_etichetta: etich || null,
      campi: JSON.stringify(campiNorm), attivo: true,
    }
    const res = id
      ? await supabase.from('entita_tipo').update(payload).eq('id', id)
      : await supabase.from('entita_tipo').insert(payload).select('id').single()
    if (res.error) { setErr(res.error.message); return }
    await carica()                       // ricarica dal DB: conferma persistenza e stato attivo
    setSaved(true)
    setTimeout(() => setSaved(false), 2500)
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>

  return (
    <div className="pw-stack" style={{ maxWidth: 860 }}>
      <div>
        <div className="pw-row" style={{ gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="pw-eyebrow">CRM · configurazione admin</div>
          {id && <span className="pw-badge ok">Attivo: {nomeSing || '—'}{campi.length ? ` · ${campi.length} campi` : ''}</span>}
        </div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Entità collegata al contatto</h1>
        <div className="pw-muted" style={{ marginTop: 6, fontSize: 14, maxWidth: 680 }}>
          Definisci <strong>cosa</strong> si lega a un contatto in questo contesto: un <em>animale</em> (vet),
          un <em>deceduto</em> (onoranze), una <em>società</em> (horeca)… Dai un nome, aggiungi i campi e
          scegli quali chiedere <strong>sempre</strong> in fase di registrazione. L'assistente lo leggerà.
        </div>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        <div className="pw-card-head"><h3>Definizione</h3></div>
        <div className="pw-card-body pw-stack" style={{ gap: 14 }}>
          <div className="pw-row" style={{ gap: 12, flexWrap: 'wrap' }}>
            <div className="pw-field" style={{ flex: 1, minWidth: 200 }}>
              <label>Nome (singolare)</label>
              <input className="pw-input" value={nomeSing} placeholder="Es. Animale"
                onChange={e => setNomeSing(e.target.value)} />
            </div>
            <div className="pw-field" style={{ flex: 1, minWidth: 200 }}>
              <label>Nome (plurale)</label>
              <input className="pw-input" value={nomePlur} placeholder="Es. Animali"
                onChange={e => setNomePlur(e.target.value)} />
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
              title="Se attivo, la stessa entità può essere collegata a più contatti (es. un locale con più referenti, o due padroni dello stesso animale)">
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
                    onChange={e => patchCampo(i, { label: e.target.value, chiave: c.chiave || slug(e.target.value) })} />
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
                  <input className="pw-input" value={(c.opzioni || []).join(', ')} placeholder="Es. cane, gatto, coniglio"
                    onChange={e => patchCampo(i, { opzioni: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />
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
        <button className="pw-btn pw-btn-primary" onClick={salva}>Salva configurazione</button>
        {saved && <span className="pw-badge ok">Salvato ✓</span>}
      </div>

      <div className="pw-muted" style={{ fontSize: 12 }}>
        Nota: per ora questa entità <strong>affianca</strong> la Società HORECA esistente. L'assistente inizierà a
        chiederla/registrarla quando collegheremo il tool di registrazione (prossimo step).
      </div>
    </div>
  )
}
