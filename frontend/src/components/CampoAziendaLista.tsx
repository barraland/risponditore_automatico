import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'

// Editor a LISTA NUMERATA per un campo di testo della riga `azienda` (una voce per riga).
// Mostra le voci compilate + `vuoti` righe numerate vuote in coda (per aggiungerne di nuove).
// Salva come testo con una voce per riga (\n) — così finisce nel prompt come elenco.
const VUOTI = 5

function conVuoti(voci: string[]): string[] {
  return [...voci, ...Array(VUOTI).fill('')]
}

export default function CampoAziendaLista({ campo, titolo, hint, placeholder }: {
  campo: string; titolo: string; hint?: string; placeholder?: string
}) {
  const { aziendaId } = useTenant()
  const [items, setItems] = useState<string[]>(conVuoti([]))
  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function carica() {
    if (!aziendaId) return
    const { data } = await supabase.from('azienda').select(campo).eq('id', aziendaId).maybeSingle()
    const voci = ((data?.[campo] as string) || '')
      .split('\n')
      .map(s => s.replace(/^\s*\d+[.)]\s*/, '').trim())  // togli eventuale "1. " iniziale
      .filter(Boolean)
    setItems(conVuoti(voci))
  }
  useEffect(() => { carica() }, [aziendaId])

  function setItem(i: number, v: string) {
    setItems(prev => { const next = [...prev]; next[i] = v; return next })
    setOk(false)
  }

  async function salva() {
    if (!aziendaId) return
    setBusy(true); setErr(null); setOk(false)
    const puliti = items.map(s => s.trim()).filter(Boolean)
    const { error } = await supabase.from('azienda')
      .update({ [campo]: puliti.join('\n') || null }).eq('id', aziendaId)
    setBusy(false)
    if (error) { setErr(error.message); return }
    setOk(true)
    setItems(conVuoti(puliti))  // ricompatta e ripristina le 5 righe vuote in coda
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
      <div className="pw-card-body pw-stack" style={{ gap: 6 }}>
        {hint && <div className="pw-muted" style={{ fontSize: 13 }}>{hint}</div>}
        {items.map((v, i) => (
          <div key={i} className="pw-row" style={{ gap: 8, alignItems: 'center' }}>
            <span style={{ width: 22, textAlign: 'right', opacity: 0.5, fontSize: 13, flex: '0 0 auto' }}>{i + 1}.</span>
            <input className="pw-input" value={v} placeholder={placeholder}
              onChange={e => setItem(i, e.target.value)} />
          </div>
        ))}
      </div>
    </div>
  )
}
