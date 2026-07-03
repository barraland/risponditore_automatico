import { useEffect, useState } from 'react'
import { useAuth } from '../lib/auth'
import { useTenant } from '../lib/tenant'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

type Modulo = {
  chiave: string; titolo: string; ordine: number; attivo: boolean; testo: string
  default: boolean; personalizzato: boolean
}

export default function Prompt() {
  const { session } = useAuth()
  const { aziendaId } = useTenant()
  const [moduli, setModuli] = useState<Modulo[]>([])
  const [anteprima, setAnteprima] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [okKey, setOkKey] = useState<string | null>(null)
  const [showPreview, setShowPreview] = useState(false)

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${session?.access_token}` }

  async function carica() {
    if (!API) { setErr('VITE_API_BASE non configurato: serve l\'URL del backend.'); setLoading(false); return }
    setLoading(true); setErr(null)
    try {
      const res = await fetch(`${API}/api/prompt/moduli`, {
        method: 'POST', headers, body: JSON.stringify({ azienda_id: aziendaId }),
      })
      const data = await res.json()
      if (!res.ok) { setErr(data?.detail || 'Errore'); return }
      setModuli(data.moduli || []); setAnteprima(data.anteprima || '')
    } catch (e: any) { setErr(e?.message || 'Errore di rete') } finally { setLoading(false) }
  }
  useEffect(() => { carica() }, [aziendaId])

  function patch(chiave: string, campo: keyof Modulo, valore: any) {
    setModuli(ms => ms.map(m => m.chiave === chiave ? { ...m, [campo]: valore } : m))
  }

  async function salva(m: Modulo) {
    setErr(null); setOkKey(null)
    const res = await fetch(`${API}/api/prompt/modulo`, {
      method: 'POST', headers,
      body: JSON.stringify({ azienda_id: aziendaId, chiave: m.chiave, titolo: m.titolo,
                             ordine: m.ordine, attivo: m.attivo, testo: m.testo }),
    })
    const data = await res.json()
    if (!res.ok || data?.ok === false) { setErr(data?.errore || data?.detail || 'Errore nel salvataggio'); return }
    setOkKey(m.chiave); await carica()
  }

  async function toggle(m: Modulo) {
    // Salvataggio immediato del solo flag attivo.
    const nuovo = !m.attivo
    patch(m.chiave, 'attivo', nuovo)
    const res = await fetch(`${API}/api/prompt/modulo`, {
      method: 'POST', headers,
      body: JSON.stringify({ azienda_id: aziendaId, chiave: m.chiave, attivo: nuovo }),
    })
    if (!res.ok) { setErr('Errore nel salvataggio'); patch(m.chiave, 'attivo', m.attivo) }
    else await carica()
  }

  async function ripristina(m: Modulo) {
    if (!confirm(`Ripristinare il modulo «${m.titolo}» al testo di default?`)) return
    const res = await fetch(`${API}/api/prompt/modulo/reset`, {
      method: 'POST', headers, body: JSON.stringify({ azienda_id: aziendaId, chiave: m.chiave }),
    })
    if (!res.ok) { setErr('Errore nel ripristino'); return }
    await carica()
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>

  return (
    <div className="pw-stack" style={{ maxWidth: 900 }}>
      <div className="pw-between">
        <div>
          <div className="pw-eyebrow">Risponditore</div>
          <h1 style={{ fontSize: 28, marginTop: 6 }}>Prompt voce (moduli)</h1>
          <div className="pw-muted" style={{ marginTop: 6, fontSize: 14, maxWidth: 640 }}>
            Il prompt di Margherita è spezzato in moduli. I testi di default valgono per tutti i
            clienti; qui puoi <strong>disattivarli</strong>, <strong>modificarli</strong> o
            riordinarli per il cliente attivo. La conoscenza dell'azienda (cosa offriamo, come
            qualificare…) resta in <em>Configurazione assistente</em>.
          </div>
        </div>
        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setShowPreview(v => !v)}>
          {showPreview ? 'Nascondi anteprima' : 'Anteprima prompt assemblato'}
        </button>
      </div>

      {err && <div className="pw-error">{err}</div>}

      {showPreview && (
        <div className="pw-card">
          <div className="pw-card-head"><h3>Anteprima — prompt assemblato ({anteprima.length} char)</h3></div>
          <div className="pw-card-body">
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.5, margin: 0, maxHeight: 360, overflow: 'auto' }}>
              {anteprima || '(vuoto)'}
            </pre>
          </div>
        </div>
      )}

      {moduli.map(m => (
        <div className="pw-card" key={m.chiave} style={{ opacity: m.attivo ? 1 : 0.6 }}>
          <div className="pw-card-head pw-between" style={{ alignItems: 'center' }}>
            <div className="pw-row" style={{ gap: 10, alignItems: 'center' }}>
              <input type="number" className="pw-input pw-btn-sm" style={{ width: 68 }} value={m.ordine}
                title="Ordine" onChange={e => patch(m.chiave, 'ordine', Number(e.target.value))} />
              <input className="pw-input pw-btn-sm" style={{ minWidth: 220, fontWeight: 600 }} value={m.titolo}
                onChange={e => patch(m.chiave, 'titolo', e.target.value)} />
              <code style={{ fontSize: 12, opacity: 0.6 }}>{m.chiave}</code>
              {m.personalizzato && <span className="pw-tenant-tag" style={{ fontSize: 11 }}>personalizzato</span>}
            </div>
            <label className="pw-row" style={{ gap: 6, alignItems: 'center', cursor: 'pointer', whiteSpace: 'nowrap' }}>
              <input type="checkbox" checked={m.attivo} onChange={() => toggle(m)} /> attivo
            </label>
          </div>
          <div className="pw-card-body pw-stack" style={{ gap: 8 }}>
            <textarea className="pw-input" rows={Math.min(16, Math.max(4, (m.testo || '').split('\n').length + 1))}
              style={{ resize: 'vertical', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5 }}
              value={m.testo} onChange={e => patch(m.chiave, 'testo', e.target.value)} />
            <div className="pw-row" style={{ gap: 8 }}>
              <button className="pw-btn pw-btn-primary pw-btn-sm" onClick={() => salva(m)}>Salva</button>
              {m.personalizzato && (
                <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => ripristina(m)}>Ripristina default</button>
              )}
              {okKey === m.chiave && <span className="pw-badge ok" style={{ alignSelf: 'center' }}>salvato ✓</span>}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
