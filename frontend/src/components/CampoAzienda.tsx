import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'

// Editor per UN campo di testo della riga `azienda` del tenant attivo (salva su Supabase).
// Riutilizzato in Documenti (cosa offriamo, regole) e in Prompt voce (saluti, qualifica, priorità).
export default function CampoAzienda({ campo, titolo, hint, rows = 6, placeholder }: {
  campo: string; titolo: string; hint?: string; rows?: number; placeholder?: string
}) {
  const { aziendaId } = useTenant()
  const [testo, setTesto] = useState('')
  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function carica() {
    if (!aziendaId) return
    const { data } = await supabase.from('azienda').select(campo).eq('id', aziendaId).maybeSingle()
    setTesto((data?.[campo] as string) || '')
  }
  useEffect(() => { carica() }, [aziendaId])

  async function salva() {
    if (!aziendaId) return
    setBusy(true); setErr(null); setOk(false)
    const { error } = await supabase.from('azienda').update({ [campo]: testo.trim() || null }).eq('id', aziendaId)
    setBusy(false)
    if (error) setErr(error.message); else setOk(true)
  }

  return (
    <div className="pw-card">
      <div className="pw-card-head pw-between" style={{ alignItems: 'center' }}>
        <h3>{titolo}</h3>
        <div className="pw-row" style={{ gap: 8, alignItems: 'center' }}>
          {ok && <span className="pw-badge ok">salvato ✓</span>}
          <button className="pw-btn pw-btn-primary pw-btn-sm" disabled={busy} onClick={salva}>{busy ? 'Salvo…' : 'Salva'}</button>
        </div>
      </div>
      <div className="pw-card-body pw-stack" style={{ gap: 8 }}>
        {hint && <div className="pw-muted" style={{ fontSize: 13 }}>{hint}</div>}
        <textarea className="pw-input" rows={rows} placeholder={placeholder}
          style={{ resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.5 }}
          value={testo} onChange={e => { setTesto(e.target.value); setOk(false) }} />
        {err && <div className="pw-error">{err}</div>}
      </div>
    </div>
  )
}
