import { useEffect, useState } from 'react'
import { useAuth } from '../lib/auth'
import { useTenant } from '../lib/tenant'
import { supabase } from '../lib/supabase'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

const CANALI: [string, string][] = [['voce', 'Voce'], ['whatsapp', 'WhatsApp'], ['mail', 'Mail']]
const AUDIENCES: [string, string][] = [['cliente', 'Quando parli con un CLIENTE'], ['admin', 'Quando parli con un ADMIN']]

// Saluto d'apertura: testo BASE (colonna azienda.<slot>) + varianti per canale (azienda.saluto_varianti).
// Voce = prima frase della telefonata; WhatsApp = incipit della prima risposta del bot. Variante vuota
// su un canale => si usa il testo base (come i moduli).
const SALUTO_CANALI: [string, string][] = [['whatsapp', 'WhatsApp'], ['voce', 'Voce']]

type SlotSaluto = { slot: string; titolo: string; hint: string; ph: string }
const SALUTI_SLOT: Record<string, SlotSaluto[]> = {
  cliente: [
    { slot: 'saluto', titolo: 'Primo saluto (cliente riconosciuto)',
      hint: 'Segnaposto: {nome} {cognome} {azienda}. Vuoto = saluto predefinito.',
      ph: 'Es. Buongiorno {cognome}, sono l\'assistente della clinica, come posso aiutarla?' },
    { slot: 'saluto_sconosciuto', titolo: 'Primo saluto (contatto sconosciuto)',
      hint: 'Qui {nome} è vuoto: non usarlo.',
      ph: 'Es. Buongiorno, sono l\'assistente della clinica, come posso aiutarla?' },
  ],
  admin: [
    { slot: 'saluto_admin', titolo: 'Primo saluto (amministratore)',
      hint: 'Segnaposto: {azienda}. Vuoto = saluto predefinito.',
      ph: 'Es. Buongiorno, sono l\'assistente. Vuole lasciare un promemoria per un cliente?' },
  ],
}

type VariantiSaluto = Record<string, Record<string, string>>

function CardSaluto({ meta, base, setBase, varianti, setVarianti, onSalva, busy, ok }: {
  meta: SlotSaluto
  base: Record<string, string>; setBase: (f: (b: Record<string, string>) => Record<string, string>) => void
  varianti: VariantiSaluto; setVarianti: (f: (v: VariantiSaluto) => VariantiSaluto) => void
  onSalva: (slot: string) => void; busy: boolean; ok: boolean
}) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState('whatsapp')
  const slot = meta.slot
  const vslot = varianti[slot] || {}
  const nVar = SALUTO_CANALI.filter(([c]) => (vslot[c] || '').trim()).length

  return (
    <div className="pw-card">
      <div className="pw-card-head pw-between" style={{ alignItems: 'center' }}>
        <h3>{meta.titolo}</h3>
        <div className="pw-row" style={{ gap: 8, alignItems: 'center' }}>
          {ok && <span className="pw-badge ok">salvato ✓</span>}
          <button className="pw-btn pw-btn-primary pw-btn-sm" disabled={busy} onClick={() => onSalva(slot)}>{busy ? 'Salvo…' : 'Salva'}</button>
        </div>
      </div>
      <div className="pw-card-body pw-stack" style={{ gap: 8 }}>
        <div className="pw-muted" style={{ fontSize: 13 }}>{meta.hint}</div>
        <textarea className="pw-input" rows={2} placeholder={meta.ph}
          style={{ resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.5 }}
          value={base[slot] || ''} onChange={e => setBase(b => ({ ...b, [slot]: e.target.value }))} />

        <button className="pw-btn pw-btn-ghost pw-btn-sm" style={{ alignSelf: 'flex-start' }}
          onClick={() => setOpen(o => !o)}>
          {open ? '▾' : '▸'} Varianti per canale{nVar ? ` (${nVar})` : ''}
        </button>
        {open && (
          <div className="pw-stack" style={{ gap: 6, borderLeft: '2px solid var(--border, #333)', paddingLeft: 12 }}>
            <div className="pw-row" style={{ gap: 6 }}>
              {SALUTO_CANALI.map(([c, lab]) => (
                <button key={c} className={`pw-btn pw-btn-sm ${tab === c ? 'pw-btn-primary' : 'pw-btn-ghost'}`}
                  onClick={() => setTab(c)}>{lab}{(vslot[c] || '').trim() ? ' •' : ''}</button>
              ))}
            </div>
            <textarea className="pw-input" rows={2} placeholder="(vuoto = usa il testo base qui sopra)"
              style={{ resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.5 }}
              value={vslot[tab] || ''}
              onChange={e => setVarianti(v => ({ ...v, [slot]: { ...(v[slot] || {}), [tab]: e.target.value } }))} />
            <div className="pw-muted" style={{ fontSize: 12 }}>
              La variante sostituisce il saluto base solo su quel canale. Su <b>WhatsApp</b> è l'incipit
              della prima risposta del bot: evita "come posso aiutarla?" se il cliente ha già scritto.
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function SezioneSaluti({ audience, aziendaId }: { audience: string; aziendaId: number | null }) {
  const [base, setBase] = useState<Record<string, string>>({})
  const [varianti, setVarianti] = useState<VariantiSaluto>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!aziendaId) return
    ;(async () => {
      // Prova col campo varianti; se la colonna non esiste ancora (migrazione non lanciata) ricadi
      // sui soli saluti base, così l'editor resta comunque utilizzabile.
      let data: any = null
      const full = await supabase.from('azienda')
        .select('saluto,saluto_sconosciuto,saluto_admin,saluto_varianti').eq('id', aziendaId).maybeSingle()
      if (full.error) {
        setErr('Colonna «saluto_varianti» assente: lancia la migrazione per usare le varianti per canale.')
        const b = await supabase.from('azienda')
          .select('saluto,saluto_sconosciuto,saluto_admin').eq('id', aziendaId).maybeSingle()
        data = b.data
      } else {
        data = full.data
      }
      setBase({
        saluto: (data?.saluto as string) || '',
        saluto_sconosciuto: (data?.saluto_sconosciuto as string) || '',
        saluto_admin: (data?.saluto_admin as string) || '',
      })
      let v: VariantiSaluto = {}
      try { v = data?.saluto_varianti ? JSON.parse(data.saluto_varianti as string) : {} } catch { v = {} }
      setVarianti(v && typeof v === 'object' ? v : {})
    })()
  }, [aziendaId])

  async function salva(slot: string) {
    if (!aziendaId) return
    setBusy(slot); setErr(null); setOk(null)
    // Ripulisci le varianti vuote prima di salvare (nessun canale vuoto nel JSON).
    const vClean: VariantiSaluto = {}
    for (const [sl, canali] of Object.entries(varianti)) {
      const inner: Record<string, string> = {}
      for (const [c, t] of Object.entries(canali || {})) if ((t || '').trim()) inner[c] = (t as string).trim()
      if (Object.keys(inner).length) vClean[sl] = inner
    }
    const { error } = await supabase.from('azienda').update({
      [slot]: (base[slot] || '').trim() || null,
      saluto_varianti: Object.keys(vClean).length ? JSON.stringify(vClean) : null,
    }).eq('id', aziendaId)
    setBusy(null)
    if (error) setErr(error.message); else setOk(slot)
  }

  return <>
    {(SALUTI_SLOT[audience] || []).map(m => (
      <CardSaluto key={m.slot} meta={m} base={base} setBase={setBase}
        varianti={varianti} setVarianti={setVarianti} onSalva={salva}
        busy={busy === m.slot} ok={ok === m.slot} />
    ))}
    {err && <div className="pw-error">{err}</div>}
  </>
}

type Modulo = {
  chiave: string; titolo: string; ordine: number; attivo: boolean; testo: string
  canali: string[]; testi: Record<string, string>; audience: string; default: boolean; personalizzato: boolean
}

function ModuloCard({ m, onPatch, onSalva, onToggle, onRipristina }: {
  m: Modulo
  onPatch: (chiave: string, patch: Partial<Modulo>) => void
  onSalva: (m: Modulo) => void
  onToggle: (m: Modulo) => void
  onRipristina: (m: Modulo) => void
}) {
  const [varianti, setVarianti] = useState(false)
  const [tab, setTab] = useState<string>(m.canali[0] || 'voce')
  const nVarianti = Object.values(m.testi || {}).filter(t => (t || '').trim()).length

  function toggleCanale(c: string) {
    const set = new Set(m.canali)
    set.has(c) ? set.delete(c) : set.add(c)
    onPatch(m.chiave, { canali: CANALI.map(([k]) => k).filter(k => set.has(k)) })
  }

  return (
    <div className="pw-card" style={{ opacity: m.attivo ? 1 : 0.6 }}>
      <div className="pw-card-head pw-between" style={{ alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <div className="pw-row" style={{ gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <input type="number" className="pw-input pw-btn-sm" style={{ width: 64 }} value={m.ordine}
            title="Ordine" onChange={e => onPatch(m.chiave, { ordine: Number(e.target.value) })} />
          <input className="pw-input pw-btn-sm" style={{ minWidth: 200, fontWeight: 600 }} value={m.titolo}
            onChange={e => onPatch(m.chiave, { titolo: e.target.value })} />
          <code style={{ fontSize: 12, opacity: 0.6 }}>{m.chiave}</code>
          {m.personalizzato && <span className="pw-tenant-tag" style={{ fontSize: 11 }}>personalizzato</span>}
        </div>
        <div className="pw-row" style={{ gap: 12, alignItems: 'center' }}>
          {CANALI.map(([k, lab]) => (
            <label key={k} className="pw-row" style={{ gap: 4, alignItems: 'center', cursor: 'pointer', fontSize: 13 }}
              title={`Applica questo modulo al canale ${lab}`}>
              <input type="checkbox" checked={m.canali.includes(k)} onChange={() => toggleCanale(k)} /> {lab}
            </label>
          ))}
          <label className="pw-row" style={{ gap: 6, alignItems: 'center', cursor: 'pointer', whiteSpace: 'nowrap' }}>
            <input type="checkbox" checked={m.attivo} onChange={() => onToggle(m)} /> attivo
          </label>
        </div>
      </div>
      <div className="pw-card-body pw-stack" style={{ gap: 8 }}>
        <textarea className="pw-input" rows={Math.min(16, Math.max(4, (m.testo || '').split('\n').length + 1))}
          style={{ resize: 'vertical', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5 }}
          value={m.testo} onChange={e => onPatch(m.chiave, { testo: e.target.value })} />

        <button className="pw-btn pw-btn-ghost pw-btn-sm" style={{ alignSelf: 'flex-start' }}
          onClick={() => setVarianti(v => !v)}>
          {varianti ? '▾' : '▸'} Varianti per canale{nVarianti ? ` (${nVarianti})` : ''}
        </button>
        {varianti && (
          <div className="pw-stack" style={{ gap: 6, borderLeft: '2px solid var(--border, #333)', paddingLeft: 12 }}>
            <div className="pw-row" style={{ gap: 6 }}>
              {m.canali.map(c => (
                <button key={c} className={`pw-btn pw-btn-sm ${tab === c ? 'pw-btn-primary' : 'pw-btn-ghost'}`}
                  onClick={() => setTab(c)}>
                  {CANALI.find(([k]) => k === c)?.[1] || c}{(m.testi[c] || '').trim() ? ' •' : ''}
                </button>
              ))}
            </div>
            {m.canali.includes(tab) ? (
              <textarea className="pw-input" rows={6}
                style={{ resize: 'vertical', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5 }}
                placeholder="(vuoto = usa il testo base qui sopra)"
                value={m.testi[tab] || ''}
                onChange={e => onPatch(m.chiave, { testi: { ...m.testi, [tab]: e.target.value } })} />
            ) : <div className="pw-muted" style={{ fontSize: 13 }}>Attiva prima il canale per questo modulo.</div>}
            <div className="pw-muted" style={{ fontSize: 12 }}>
              La variante sostituisce il testo base solo sul canale selezionato. Vuota = usa il base.
            </div>
          </div>
        )}

        <div className="pw-row" style={{ gap: 8 }}>
          <button className="pw-btn pw-btn-primary pw-btn-sm" onClick={() => onSalva(m)}>Salva</button>
          {m.personalizzato && (
            <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => onRipristina(m)}>Ripristina default</button>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Prompt() {
  const { session } = useAuth()
  const { aziendaId } = useTenant()
  const [moduli, setModuli] = useState<Modulo[]>([])
  const [anteprima, setAnteprima] = useState('')
  const [canale, setCanale] = useState('voce')
  const [audience, setAudience] = useState('cliente')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [showPreview, setShowPreview] = useState(false)

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${session?.access_token}` }

  async function carica() {
    if (!API) { setErr('VITE_API_BASE non configurato: serve l\'URL del backend.'); setLoading(false); return }
    setErr(null)
    try {
      const res = await fetch(`${API}/api/prompt/moduli`, {
        method: 'POST', headers, body: JSON.stringify({ azienda_id: aziendaId, canale, audience }),
      })
      const data = await res.json()
      if (!res.ok) { setErr(data?.detail || 'Errore'); return }
      setModuli(data.moduli || []); setAnteprima(data.anteprima || '')
    } catch (e: any) { setErr(e?.message || 'Errore di rete') } finally { setLoading(false) }
  }
  useEffect(() => { carica() }, [aziendaId, canale, audience])

  function patch(chiave: string, p: Partial<Modulo>) {
    setModuli(ms => ms.map(m => m.chiave === chiave ? { ...m, ...p } : m))
  }

  async function salva(m: Modulo) {
    setErr(null)
    const res = await fetch(`${API}/api/prompt/modulo`, {
      method: 'POST', headers,
      body: JSON.stringify({ azienda_id: aziendaId, chiave: m.chiave, titolo: m.titolo, ordine: m.ordine,
                             attivo: m.attivo, testo: m.testo, canali: m.canali, testi_canale: m.testi,
                             audience: m.audience }),
    })
    const data = await res.json()
    if (!res.ok || data?.ok === false) { setErr(data?.errore || data?.detail || 'Errore nel salvataggio'); return }
    await carica()
  }

  async function toggle(m: Modulo) {
    const nuovo = !m.attivo
    patch(m.chiave, { attivo: nuovo })
    const res = await fetch(`${API}/api/prompt/modulo`, {
      method: 'POST', headers, body: JSON.stringify({ azienda_id: aziendaId, chiave: m.chiave, attivo: nuovo }),
    })
    if (!res.ok) { setErr('Errore nel salvataggio'); patch(m.chiave, { attivo: m.attivo }) } else await carica()
  }

  async function ripristina(m: Modulo) {
    if (!confirm(`Ripristinare il modulo «${m.titolo}» al default (testo, canali e varianti)?`)) return
    const res = await fetch(`${API}/api/prompt/modulo/reset`, {
      method: 'POST', headers, body: JSON.stringify({ azienda_id: aziendaId, chiave: m.chiave }),
    })
    if (!res.ok) { setErr('Errore nel ripristino'); return }
    await carica()
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>

  return (
    <div className="pw-stack" style={{ maxWidth: 900 }}>
      <div className="pw-between" style={{ flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div className="pw-eyebrow">Risponditore</div>
          <h1 style={{ fontSize: 28, marginTop: 6 }}>Assistente</h1>
          <div className="pw-muted" style={{ marginTop: 6, fontSize: 14, maxWidth: 660 }}>
            Scegli il <strong>pubblico</strong> qui sotto: cambia tutto ciò che vedi. Ogni modulo si
            applica ai canali <strong>flaggati</strong> (Voce / WhatsApp / Mail) e usa un
            {' '}<strong>testo base</strong>; puoi dare a un canale un testo diverso dalle <em>Varianti</em>.
          </div>
        </div>
        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setShowPreview(v => !v)}>
          {showPreview ? 'Nascondi anteprima' : 'Anteprima prompt'}
        </button>
      </div>

      {/* Interruttore PUBBLICO: cliente vs admin. Cambia i moduli e i campi sotto. */}
      <div className="pw-row" style={{ gap: 6 }}>
        {AUDIENCES.map(([k, lab]) => (
          <button key={k} className={`pw-btn pw-btn-sm ${audience === k ? 'pw-btn-primary' : 'pw-btn-ghost'}`}
            onClick={() => setAudience(k)}>{lab}</button>
        ))}
      </div>

      {err && <div className="pw-error">{err}</div>}

      <SezioneSaluti audience={audience} aziendaId={aziendaId} />

      {showPreview && (
        <div className="pw-card">
          <div className="pw-card-head pw-between" style={{ alignItems: 'center' }}>
            <h3>Anteprima — {CANALI.find(([k]) => k === canale)?.[1]} ({anteprima.length} char)</h3>
            <div className="pw-row" style={{ gap: 6 }}>
              {CANALI.map(([k, lab]) => (
                <button key={k} className={`pw-btn pw-btn-sm ${canale === k ? 'pw-btn-primary' : 'pw-btn-ghost'}`}
                  onClick={() => setCanale(k)}>{lab}</button>
              ))}
            </div>
          </div>
          <div className="pw-card-body">
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.5, margin: 0, maxHeight: 360, overflow: 'auto' }}>
              {anteprima || '(nessun modulo attivo per questo canale)'}
            </pre>
          </div>
        </div>
      )}

      {moduli.filter(m => m.audience === audience).map(m => (
        <ModuloCard key={m.chiave} m={m} onPatch={patch} onSalva={salva} onToggle={toggle} onRipristina={ripristina} />
      ))}
    </div>
  )
}
