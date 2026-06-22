import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'

const AREA: React.CSSProperties = { resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.5 }

export default function Assistente() {
  const [az, setAz] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState(false)

  useEffect(() => {
    supabase.from('azienda').select('*').order('id').limit(1).maybeSingle()
      .then(({ data, error }) => { if (error) setErr(error.message); else setAz(data || {}); setLoading(false) })
  }, [])

  const set = (k: string, v: string) => { setAz({ ...az, [k]: v }); setOk(false) }

  async function salva() {
    if (!az?.id) { setErr('Riga azienda non trovata su Supabase.'); return }
    setBusy(true); setErr(null); setOk(false)
    const { error } = await supabase.from('azienda').update({
      nome: (az.nome || '').trim() || az.nome,
      telefono: (az.telefono || '').trim() || null,
      descrizione_servizi: (az.descrizione_servizi || '').trim() || null,
      info_qualificazione: (az.info_qualificazione || '').trim() || null,
      criteri_priorita: (az.criteri_priorita || '').trim() || null,
      istruzioni_admin: (az.istruzioni_admin || '').trim() || null,
      regole_commerciali: (az.regole_commerciali || '').trim() || null,
    }).eq('id', az.id)
    setBusy(false)
    if (error) setErr(error.message); else setOk(true)
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>
  if (err && !az) return <div className="pw-error">{err}</div>

  return (
    <div className="pw-stack" style={{ maxWidth: 820 }}>
      <div className="pw-between">
        <div>
          <div className="pw-eyebrow">Risponditore</div>
          <h1 style={{ fontSize: 28, marginTop: 6 }}>Configurazione assistente</h1>
          <div className="pw-muted" style={{ marginTop: 6, fontSize: 14 }}>
            Questi testi guidano l'assistente su voce, WhatsApp e ElevenLabs (via la variabile <code>{'{{configurazione}}'}</code>).
          </div>
        </div>
        <div className="pw-row">
          {ok && <span className="pw-badge ok">salvato ✓</span>}
          <button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{busy ? 'Salvo…' : 'Salva'}</button>
        </div>
      </div>

      <div className="pw-card"><div className="pw-card-head"><h3>Azienda</h3></div>
        <div className="pw-card-body pw-row" style={{ gap: 12 }}>
          <div className="pw-field" style={{ flex: 2 }}><label>Nome</label><input className="pw-input" value={az.nome || ''} onChange={e => set('nome', e.target.value)} /></div>
          <div className="pw-field" style={{ flex: 1 }}><label>Telefono</label><input className="pw-input" value={az.telefono || ''} onChange={e => set('telefono', e.target.value)} /></div>
        </div>
      </div>

      <Campo titolo="Cosa offriamo" hint="Prodotti/servizi, dove operate, tempi di consegna, ordine minimo… L'assistente risponde SOLO con queste info."
        value={az.descrizione_servizi || ''} onChange={v => set('descrizione_servizi', v)} rows={6} />

      <Campo titolo="Come qualificare il lead" hint="Informazioni minime da raccogliere durante la conversazione (nome, società, ruolo, contatti, esigenza…)."
        value={az.info_qualificazione || ''} onChange={v => set('info_qualificazione', v)} rows={5} />

      <Campo titolo="Come assegnare la priorità" hint="Cosa rende un lead alta / media / bassa priorità."
        value={az.criteri_priorita || ''} onChange={v => set('criteri_priorita', v)} rows={4} />

      <Campo titolo="Istruzioni libere (il tuo prompt)" hint="Indicazioni in linguaggio naturale su tono, regole, cosa dire o non dire. È il testo che sostituisce il 'mega prompt': vale per tutti i canali."
        value={az.istruzioni_admin || ''} onChange={v => set('istruzioni_admin', v)} rows={10} />

      <Campo titolo="Regole commerciali e promozioni" hint="Prezzi, sconti e offerte che l'assistente applica sempre — rispondendo sui prezzi e registrando ordini. Es: «compri 10 casse di birra, 5 in omaggio»; «3x2 sui pelati fino al 14/08/2026»; «sconto 10% sopra i 500€». Per calcoli ambigui chiede conferma."
        value={az.regole_commerciali || ''} onChange={v => set('regole_commerciali', v)} rows={10} />

      {err && <div className="pw-error">{err}</div>}
      <div className="pw-row" style={{ justifyContent: 'flex-end' }}>
        {ok && <span className="pw-badge ok">salvato ✓</span>}
        <button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{busy ? 'Salvo…' : 'Salva'}</button>
      </div>
    </div>
  )
}

function Campo({ titolo, hint, value, onChange, rows }: { titolo: string; hint: string; value: string; onChange: (v: string) => void; rows: number }) {
  return (
    <div className="pw-card">
      <div className="pw-card-head"><h3>{titolo}</h3></div>
      <div className="pw-card-body pw-stack" style={{ gap: 8 }}>
        <div className="pw-muted" style={{ fontSize: 13 }}>{hint}</div>
        <textarea className="pw-input" rows={rows} style={AREA} value={value} onChange={e => onChange(e.target.value)} />
      </div>
    </div>
  )
}
