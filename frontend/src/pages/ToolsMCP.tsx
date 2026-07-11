import { useEffect, useState } from 'react'
import { useAuth } from '../lib/auth'

const API = (import.meta.env.VITE_API_BASE as string || '').replace(/\/$/, '')

type Tool = { nome: string; parametri: any; default: string; override: string }

function Parametri({ schema }: { schema: any }) {
  const props = (schema && schema.properties) || {}
  const req = new Set<string>((schema && schema.required) || [])
  const keys = Object.keys(props)
  if (!keys.length) return <div className="pw-muted" style={{ fontSize: 12.5 }}>nessun parametro</div>
  return (
    <div className="pw-stack" style={{ gap: 3 }}>
      {keys.map(k => {
        const p = props[k] || {}
        return (
          <div key={k} style={{ fontSize: 12.5 }}>
            <code>{k}</code>{' '}
            <span className="pw-muted">{p.type || '?'}{req.has(k) ? ' · obbligatorio' : ''}</span>
            {p.description ? <span style={{ color: 'var(--fg-3)' }}> — {p.description}</span> : null}
          </div>
        )
      })}
    </div>
  )
}

function ToolCard({ t, onSave }: { t: Tool; onSave: (nome: string, desc: string) => Promise<void> }) {
  const [testo, setTesto] = useState(t.override || t.default)
  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState(false)
  const modificato = !!t.override
  const cambiato = testo.trim() !== (t.override || t.default).trim()

  async function salva(reset = false) {
    setBusy(true); setOk(false)
    await onSave(t.nome, reset ? '' : testo)
    if (reset) setTesto(t.default)
    setBusy(false); setOk(true); setTimeout(() => setOk(false), 2000)
  }

  return (
    <div className="pw-card">
      <div className="pw-card-head pw-between" style={{ alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <div className="pw-row" style={{ gap: 10, alignItems: 'center' }}>
          <code style={{ fontSize: 14 }}>{t.nome}</code>
          {modificato && <span className="pw-badge warn">descrizione modificata</span>}
        </div>
        <div className="pw-row" style={{ gap: 8 }}>
          {ok && <span className="pw-badge ok">salvato ✓</span>}
          {modificato && <button className="pw-btn pw-btn-ghost pw-btn-sm" disabled={busy} onClick={() => salva(true)}>Ripristina default</button>}
          <button className="pw-btn pw-btn-primary pw-btn-sm" disabled={busy || !cambiato} onClick={() => salva(false)}>Salva</button>
        </div>
      </div>
      <div className="pw-card-body pw-stack" style={{ gap: 10 }}>
        <div>
          <div className="pw-muted" style={{ fontSize: 12, marginBottom: 4 }}>Descrizione (vista dall'agente) — editabile</div>
          <textarea className="pw-input" rows={Math.min(10, Math.max(3, testo.split('\n').length + 1))}
            style={{ resize: 'vertical', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5 }}
            value={testo} onChange={e => setTesto(e.target.value)}
            placeholder={t.default} />
          <div className="pw-muted" style={{ fontSize: 11, marginTop: 3 }}>
            Svuota e salva per tornare alla descrizione di default.
          </div>
        </div>
        <div>
          <div className="pw-muted" style={{ fontSize: 12, marginBottom: 4 }}>Parametri di input (sola lettura)</div>
          <Parametri schema={t.parametri} />
        </div>
      </div>
    </div>
  )
}

export default function ToolsMCP() {
  const { session } = useAuth()
  const [tools, setTools] = useState<Tool[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${session?.access_token}` }

  async function carica() {
    if (!API) { setErr('VITE_API_BASE non configurato: serve l\'URL del backend.'); setLoading(false); return }
    setErr(null)
    try {
      const res = await fetch(`${API}/api/tools`, { headers })
      const data = await res.json()
      if (!res.ok) { setErr(data?.detail || 'Errore'); return }
      if (data.errore) setErr(data.errore)
      setTools(data.tools || [])
    } catch (e: any) { setErr(e?.message || 'Errore di rete') } finally { setLoading(false) }
  }
  useEffect(() => { carica() }, [])

  async function salva(nome: string, desc: string) {
    const res = await fetch(`${API}/api/tools/salva`, {
      method: 'POST', headers, body: JSON.stringify({ tool_name: nome, descrizione: desc }),
    })
    if (!res.ok) { setErr('Errore nel salvataggio'); return }
    await carica()
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>

  return (
    <div className="pw-stack" style={{ maxWidth: 900 }}>
      <div>
        <div className="pw-eyebrow">Assistente · configurazione admin</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>MCP server</h1>
        <div className="pw-muted" style={{ marginTop: 6, fontSize: 14, maxWidth: 700 }}>
          Gli strumenti che l'assistente può usare. Ogni tool ha una <strong>descrizione</strong> che il
          modello legge per decidere quando usarlo — qui la <strong>vedi e la modifichi</strong>. L'override
          vale su tutti i canali (voce, WhatsApp, ElevenLabs). I parametri sono in sola lettura.
        </div>
      </div>

      {err && <div className="pw-error">{err}</div>}

      {tools.length === 0 && !err
        ? <div className="pw-card"><div className="pw-empty">Nessun tool disponibile.</div></div>
        : tools.map(t => <ToolCard key={t.nome} t={t} onSave={salva} />)}
    </div>
  )
}
