import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'

type Riga = {
  id: number
  nome: string
  numeri_voce: string | null
  whatsapp_phone_id: string | null
}

// Colonne "profilo" dell'azienda da clonare in un nuovo cliente (prompt + impostazioni, NON identità/routing).
// Solo quelle presenti nella riga sorgente vengono copiate → nessun errore se una colonna non esiste.
const CLONE_COLS = [
  'saluto', 'saluto_sconosciuto', 'saluto_admin', 'saluto_varianti',
  'descrizione_servizi', 'istruzioni_admin', 'regole_commerciali', 'prompt_whatsapp',
  'contatto_obbligatori', 'commercio_labels', 'mostra_ordini', 'mostra_agenti', 'mostra_calendario',
]

export default function Clienti() {
  const { isSuperAdmin, ready, reload, aziende, aziendaId } = useTenant()
  const [righe, setRighe] = useState<Riga[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [nuovo, setNuovo] = useState({ nome: '', numeri_voce: '', whatsapp_phone_id: '' })
  const [copiaDa, setCopiaDa] = useState<number | ''>('')
  useEffect(() => { if (aziendaId) setCopiaDa(aziendaId) }, [aziendaId])

  async function carica() {
    setLoading(true)
    const { data, error } = await supabase
      .from('azienda')
      .select('id, nome, numeri_voce, whatsapp_phone_id')
      .order('id')
    if (error) setErr(error.message)
    else setRighe((data || []) as Riga[])
    setLoading(false)
  }
  useEffect(() => { carica() }, [])

  async function crea() {
    setErr(null)
    if (!nuovo.nome.trim()) { setErr('Il nome del cliente è obbligatorio.'); return }

    // 1) Impostazioni azienda copiate dal tenant-template scelto (prompt/saluti/ecc.).
    const base: any = {}
    if (copiaDa) {
      const { data: src } = await supabase.from('azienda').select('*').eq('id', copiaDa).maybeSingle()
      if (src) for (const k of CLONE_COLS) if (k in (src as any)) base[k] = (src as any)[k]
    }

    // 2) Crea l'azienda (identità/routing dal form; il resto dal template).
    const { data: created, error } = await supabase.from('azienda').insert({
      ...base,
      nome: nuovo.nome.trim(),
      numeri_voce: nuovo.numeri_voce.trim() || null,
      whatsapp_phone_id: nuovo.whatsapp_phone_id.trim() || null,
    }).select('id').single()
    if (error) { setErr(error.message); return }

    // 3) Clona i MODULI prompt (testo + ordine + attivo + canali/varianti) dal template.
    if (copiaDa && created?.id) {
      const { data: mods } = await supabase.from('prompt_modulo')
        .select('chiave, titolo, ordine, attivo, testo, canali, testi_canale').eq('azienda_id', copiaDa)
      if (mods && mods.length) {
        const copie = mods.map((m: any) => ({ ...m, azienda_id: created.id }))
        const { error: e2 } = await supabase.from('prompt_modulo').insert(copie)
        if (e2) { setErr(`Cliente creato, ma copia dei moduli prompt fallita: ${e2.message}`) }
      }
    }

    setNuovo({ nome: '', numeri_voce: '', whatsapp_phone_id: '' })
    await carica()
    await reload() // aggiorna il selettore in alto
  }

  async function elimina(r: Riga) {
    if (aziende.length <= 1) { setErr('Non puoi eliminare l\'unico cliente rimasto.'); return }
    if (!confirm(`Eliminare il cliente "${r.nome}"?\n\nÈ possibile solo se NON ha dati associati (contatti, ordini, documenti…). L'operazione non è reversibile.`)) return
    const { error } = await supabase.from('azienda').delete().eq('id', r.id)
    if (error) {
      // FK violation: il tenant ha ancora dati collegati (protezione anti-cancellazione).
      setErr(`Impossibile eliminare "${r.nome}": ha dati associati (contatti, ordini, documenti…). Svuota prima i suoi dati. [${error.message}]`)
      return
    }
    setErr(null)
    await carica()
    await reload() // se era il tenant attivo, il selettore passa a un altro
  }

  async function salvaCampo(r: Riga, campo: 'nome' | 'numeri_voce' | 'whatsapp_phone_id', valore: string) {
    const v = valore.trim()
    if (campo === 'nome' && !v) { setErr('Il nome del cliente non può essere vuoto.'); await carica(); return }
    const { error } = await supabase.from('azienda').update({ [campo]: campo === 'nome' ? v : (v || null) }).eq('id', r.id)
    if (error) setErr(error.message)
    else { setErr(null); await carica(); await reload() } // reload aggiorna anche il selettore in alto
  }

  if (ready && !isSuperAdmin) {
    return <div className="pw-card">Accesso riservato al super-admin.</div>
  }

  return (
    <div className="pw-stack">
      <div>
        <div className="pw-eyebrow">Multi-tenant</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Clienti</h1>
        <p className="pw-muted" style={{ maxWidth: 640 }}>
          Ogni cliente è un <strong>tenant</strong> isolato. Il tenant delle telefonate è il
          <em> numero chiamato</em>: elenca qui i numeri di voce (uno per riga o separati da virgola,
          solo cifre) e il <em>Phone Number ID</em> di WhatsApp per instradare le conversazioni.
        </p>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        <h3 style={{ marginTop: 0 }}>Nuovo cliente</h3>
        <div className="pw-row" style={{ flexWrap: 'wrap', gap: 8 }}>
          <input className="pw-input" placeholder="Nome cliente" style={{ maxWidth: 240 }}
            value={nuovo.nome} onChange={e => setNuovo({ ...nuovo, nome: e.target.value })} />
          <input className="pw-input" placeholder="Numeri voce (es. +3902…, +3906…)" style={{ maxWidth: 260 }}
            value={nuovo.numeri_voce} onChange={e => setNuovo({ ...nuovo, numeri_voce: e.target.value })} />
          <input className="pw-input" placeholder="WhatsApp Phone Number ID" style={{ maxWidth: 220 }}
            value={nuovo.whatsapp_phone_id} onChange={e => setNuovo({ ...nuovo, whatsapp_phone_id: e.target.value })} />
          <select className="pw-select" style={{ maxWidth: 240 }} value={copiaDa}
            title="Copia prompt e impostazioni da un cliente esistente" onChange={e => setCopiaDa(e.target.value ? Number(e.target.value) : '')}>
            <option value="">— profilo vuoto (default di sistema) —</option>
            {aziende.map(a => <option key={a.id} value={a.id}>Parti da: {a.nome}</option>)}
          </select>
          <button className="pw-btn pw-btn-primary" onClick={crea}>Crea cliente</button>
        </div>
        <div className="pw-muted" style={{ fontSize: 12, marginTop: 8 }}>
          «Parti da» copia i moduli prompt (testo, numero d'ordine, flag attivo, voce/WhatsApp) e le impostazioni
          (saluti, descrizione, istruzioni, ecc.) dal cliente scelto. Il nuovo profilo poi si personalizza in autonomia.
        </div>
      </div>

      {loading ? <div className="pw-spinner">Caricamento…</div> : (
        <table className="pw-table">
          <thead>
            <tr><th>ID</th><th>Nome</th><th>Numeri voce</th><th>WhatsApp Phone ID</th><th></th></tr>
          </thead>
          <tbody>
            {righe.map(r => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>
                  <input className="pw-input pw-btn-sm" style={{ fontWeight: 600 }} defaultValue={r.nome}
                    title="Rinomina il cliente (invio o click fuori per salvare)"
                    onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
                    onBlur={e => e.target.value.trim() !== r.nome && salvaCampo(r, 'nome', e.target.value)} />
                </td>
                <td>
                  <input className="pw-input pw-btn-sm" defaultValue={r.numeri_voce || ''}
                    onBlur={e => e.target.value !== (r.numeri_voce || '') && salvaCampo(r, 'numeri_voce', e.target.value)} />
                </td>
                <td>
                  <input className="pw-input pw-btn-sm" defaultValue={r.whatsapp_phone_id || ''}
                    onBlur={e => e.target.value !== (r.whatsapp_phone_id || '') && salvaCampo(r, 'whatsapp_phone_id', e.target.value)} />
                </td>
                <td style={{ textAlign: 'right' }}>
                  <button className="pw-btn pw-btn-ghost pw-btn-sm" style={{ color: 'var(--danger)' }}
                    disabled={aziende.length <= 1} title={aziende.length <= 1 ? 'Ultimo cliente rimasto' : 'Elimina cliente'}
                    onClick={() => elimina(r)}>Elimina</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
