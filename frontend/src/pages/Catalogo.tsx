import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'
import { useCommercioLabels, DEFAULT_LABELS, type CommercioLabels } from '../lib/commercioLabels'
import Modal from '../components/Modal'

type Item = { id: number; name: string; description: string | null; price: number | string | null }

const euro = (v: number | string | null) =>
  v == null || v === '' ? '—' : `€ ${Number(v).toFixed(2)}`

export default function Catalogo() {
  const { aziendaId } = useTenant()
  const [labels, reloadLabels] = useCommercioLabels(aziendaId)
  const [righe, setRighe] = useState<Item[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [edit, setEdit] = useState<Item | null>(null)
  const [nuovo, setNuovo] = useState(false)
  const [rinomina, setRinomina] = useState(false)

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    setLoading(true); setErr(null)
    const { data, error } = await supabase.from('catalog_items')
      .select('id, name, description, price').eq('azienda_id', aziendaId).order('name')
    if (error) setErr(error.message); else setRighe((data as Item[]) || [])
    setLoading(false)
  }
  useEffect(() => { carica() }, [aziendaId])

  async function elimina(r: Item) {
    if (!confirm(`Eliminare «${r.name}»?`)) return
    const { error } = await supabase.from('catalog_items').delete().eq('id', r.id)
    if (error) setErr(error.message); else carica()
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>

  return (
    <div className="pw-stack">
      <div className="pw-between" style={{ flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div className="pw-eyebrow">Catalogo</div>
          <h1 style={{ fontSize: 28, marginTop: 6 }}>{labels.catalogo.plur}</h1>
          <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>
            Ciò che offri ai clienti. L'assistente li usa per comporre gli {labels.ordine.plur.toLowerCase()}.
          </div>
        </div>
        <div className="pw-row" style={{ gap: 8 }}>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setRinomina(true)}>✎ Rinomina etichette</button>
          <button className="pw-btn pw-btn-primary pw-btn-sm" onClick={() => setNuovo(true)}>+ {labels.catalogo.sing}</button>
        </div>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        {righe.length === 0
          ? <div className="pw-empty">Nessun/a «{labels.catalogo.sing}» ancora. Aggiungine uno.</div>
          : (
            <div style={{ overflowX: 'auto' }}>
              <table className="pw-table">
                <thead><tr><th>Nome</th><th>Descrizione</th><th>Prezzo</th><th></th></tr></thead>
                <tbody>
                  {righe.map(r => (
                    <tr key={r.id}>
                      <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{r.name}</td>
                      <td style={{ color: 'var(--fg-2)', fontSize: 13, maxWidth: 420, whiteSpace: 'pre-wrap' }}>{r.description || '—'}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>{euro(r.price)}</td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setEdit(r)}>Modifica</button>{' '}
                        <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => elimina(r)}>Elimina</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {(nuovo || edit) && (
        <EditItem item={edit} aziendaId={aziendaId} labelSing={labels.catalogo.sing}
          onClose={() => { setNuovo(false); setEdit(null) }}
          onSaved={() => { setNuovo(false); setEdit(null); carica() }} />
      )}
      {rinomina && <RinominaLabels aziendaId={aziendaId} current={labels}
        onClose={() => setRinomina(false)} onSaved={() => { setRinomina(false); reloadLabels() }} />}
    </div>
  )
}

function EditItem({ item, aziendaId, labelSing, onClose, onSaved }: {
  item: Item | null; aziendaId: number | null; labelSing: string; onClose: () => void; onSaved: () => void
}) {
  const [f, setF] = useState({
    name: item?.name || '', description: item?.description || '',
    price: item?.price != null ? String(item.price) : '',
  })
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const set = (k: string, v: string) => setF(o => ({ ...o, [k]: v }))

  async function salva() {
    if (!f.name.trim()) { setErr('Il nome è obbligatorio.'); return }
    setBusy(true); setErr(null)
    const payload: any = {
      name: f.name.trim(), description: f.description.trim() || null,
      price: f.price.trim() === '' ? null : Number(f.price),
    }
    // item_type NOT NULL con default solo lato ORM: nell'insert diretto va passato esplicitamente.
    const res = item
      ? await supabase.from('catalog_items').update(payload).eq('id', item.id)
      : await supabase.from('catalog_items').insert({ ...payload, azienda_id: aziendaId, item_type: 'PRODUCT' })
    setBusy(false)
    if (res.error) setErr(res.error.message); else onSaved()
  }

  return (
    <Modal title={item ? `Modifica ${labelSing}` : `Nuovo ${labelSing}`} onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button>
               <button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{busy ? 'Salvo…' : 'Salva'}</button></>}>
      <div className="pw-field"><label>Nome *</label>
        <input className="pw-input" value={f.name} onChange={e => set('name', e.target.value)} /></div>
      <div className="pw-field"><label>Prezzo (€)</label>
        <input className="pw-input" type="number" step="0.01" min="0" placeholder="es. 12.50"
          value={f.price} onChange={e => set('price', e.target.value)} /></div>
      <div className="pw-field"><label>Descrizione</label>
        <textarea className="pw-input" rows={3} style={{ resize: 'vertical', fontFamily: 'inherit' }}
          value={f.description} onChange={e => set('description', e.target.value)} /></div>
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}

function RinominaLabels({ aziendaId, current, onClose, onSaved }: {
  aziendaId: number | null; current: CommercioLabels; onClose: () => void; onSaved: () => void
}) {
  const [l, setL] = useState<CommercioLabels>(current)
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const set = (grp: keyof CommercioLabels, k: 'sing' | 'plur', v: string) =>
    setL(o => ({ ...o, [grp]: { ...o[grp], [k]: v } }))

  async function salva() {
    if (!aziendaId) return
    setBusy(true); setErr(null)
    const { error } = await supabase.from('azienda').update({ commercio_labels: JSON.stringify(l) }).eq('id', aziendaId)
    setBusy(false)
    if (error) setErr(error.message); else onSaved()
  }

  const gruppi: [keyof CommercioLabels, string][] = [
    ['catalogo', 'Catalogo (le cose che offri)'],
    ['ordine', 'Ordine (la richiesta del cliente)'],
    ['riga', 'Riga (voce dell\'ordine)'],
  ]
  return (
    <Modal title="Rinomina etichette (per questo cliente)" onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button>
               <button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{busy ? 'Salvo…' : 'Salva'}</button></>}>
      <div className="pw-muted" style={{ fontSize: 13 }}>
        Come chiamare queste cose nella dashboard di questo cliente. Es. HORECA: {DEFAULT_LABELS.catalogo.plur}/{DEFAULT_LABELS.ordine.plur};
        altri: «Servizi»/«Prenotazioni».
      </div>
      {gruppi.map(([grp, titolo]) => (
        <div key={grp} className="pw-field" style={{ marginTop: 8 }}>
          <label>{titolo}</label>
          <div className="pw-row" style={{ gap: 8 }}>
            <input className="pw-input" placeholder="singolare" value={l[grp].sing} onChange={e => set(grp, 'sing', e.target.value)} />
            <input className="pw-input" placeholder="plurale" value={l[grp].plur} onChange={e => set(grp, 'plur', e.target.value)} />
          </div>
        </div>
      ))}
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}
