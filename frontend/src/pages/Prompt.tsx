import { useEffect, useState } from 'react'
import { useAuth } from '../lib/auth'
import { useTenant } from '../lib/tenant'
import CampoAzienda from '../components/CampoAzienda'
import GoogleConnect from '../components/GoogleConnect'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

const CANALI: [string, string][] = [['voce', 'Voce'], ['whatsapp', 'WhatsApp'], ['mail', 'Mail'], ['admin', 'Admin']]

type Modulo = {
  chiave: string; titolo: string; ordine: number; attivo: boolean; testo: string
  canali: string[]; testi: Record<string, string>; default: boolean; personalizzato: boolean
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
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [showPreview, setShowPreview] = useState(false)

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${session?.access_token}` }

  async function carica() {
    if (!API) { setErr('VITE_API_BASE non configurato: serve l\'URL del backend.'); setLoading(false); return }
    setErr(null)
    try {
      const res = await fetch(`${API}/api/prompt/moduli`, {
        method: 'POST', headers, body: JSON.stringify({ azienda_id: aziendaId, canale }),
      })
      const data = await res.json()
      if (!res.ok) { setErr(data?.detail || 'Errore'); return }
      setModuli(data.moduli || []); setAnteprima(data.anteprima || '')
    } catch (e: any) { setErr(e?.message || 'Errore di rete') } finally { setLoading(false) }
  }
  useEffect(() => { carica() }, [aziendaId, canale])

  function patch(chiave: string, p: Partial<Modulo>) {
    setModuli(ms => ms.map(m => m.chiave === chiave ? { ...m, ...p } : m))
  }

  async function salva(m: Modulo) {
    setErr(null)
    const res = await fetch(`${API}/api/prompt/modulo`, {
      method: 'POST', headers,
      body: JSON.stringify({ azienda_id: aziendaId, chiave: m.chiave, titolo: m.titolo, ordine: m.ordine,
                             attivo: m.attivo, testo: m.testo, canali: m.canali, testi_canale: m.testi }),
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
            Saluti e criteri del lead qui sotto; poi il <strong>comportamento</strong> in moduli.
            Ogni modulo si applica ai canali <strong>flaggati</strong> (Voce / WhatsApp / Mail / Admin)
            e usa un <strong>testo base</strong>; puoi dare a un canale un testo diverso dalle
            {' '}<em>Varianti</em>. Il catalogo e le promozioni stanno in <em>Documenti</em>.
          </div>
        </div>
        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setShowPreview(v => !v)}>
          {showPreview ? 'Nascondi anteprima' : 'Anteprima prompt'}
        </button>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <CampoAzienda campo="saluto" titolo="Primo saluto (cliente riconosciuto)" rows={2}
        hint="Il primo messaggio all'apertura quando il chiamante è riconosciuto. Segnaposto: {nome} {cognome} {azienda}. Vuoto = saluto predefinito."
        placeholder="Es. Buongiorno {cognome}, sono Margherita di {azienda}, come posso aiutarla?" />
      <CampoAzienda campo="saluto_sconosciuto" titolo="Primo saluto (chiamante sconosciuto)" rows={2}
        hint="Primo messaggio quando il numero non è riconosciuto. Qui {nome} è vuoto: non usarlo."
        placeholder="Es. Buongiorno, sono Margherita di {azienda}, come posso aiutarla?" />

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

      {moduli.map(m => (
        <ModuloCard key={m.chiave} m={m} onPatch={patch} onSalva={salva} onToggle={toggle} onRipristina={ripristina} />
      ))}

      <div style={{ marginTop: 8 }}>
        <div className="pw-eyebrow">Integrazioni</div>
        <GoogleConnect />
      </div>
    </div>
  )
}
