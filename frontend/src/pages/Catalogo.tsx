import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'
import { useCommercioLabels, DEFAULT_LABELS, type CommercioLabels } from '../lib/commercioLabels'
import Modal from '../components/Modal'

type Item = {
  id: number; name: string; description: string | null; price: number | string | null
  brand?: string | null; sku?: string | null; unit_of_sale?: string | null
  vat_rate?: number | string | null; aliases?: string | null; attributes?: string | null; category_path?: string | null
}

const euro = (v: any) => (v == null || v === '' ? '—' : `€ ${Number(v).toFixed(2)}`)
const num = (s: any): number | null => {
  const v = String(s ?? '').trim().replace(',', '.')
  return v === '' || isNaN(Number(v)) ? null : Number(v)
}
const parseArr = (raw: any): string[] => {
  if (!raw) return []
  try { const v = JSON.parse(raw); return Array.isArray(v) ? v.map(String) : [] } catch { return [] }
}
const catLabel = (raw: any) => parseArr(raw).join(' › ') || '—'

export default function Catalogo() {
  const { aziendaId } = useTenant()
  const [labels, reloadLabels] = useCommercioLabels(aziendaId)
  const [righe, setRighe] = useState<Item[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [edit, setEdit] = useState<Item | null>(null)
  const [nuovo, setNuovo] = useState(false)
  const [rinomina, setRinomina] = useState(false)
  const [importa, setImporta] = useState(false)

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    setLoading(true); setErr(null)
    // select('*') è resiliente: se le colonne estese non esistono ancora (migrazione), non erra.
    const { data, error } = await supabase.from('catalog_items').select('*').eq('azienda_id', aziendaId).order('name')
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
        <div className="pw-row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setRinomina(true)}>✎ Rinomina etichette</button>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setImporta(true)}>⬆ Importa CSV</button>
          <button className="pw-btn pw-btn-primary pw-btn-sm" onClick={() => setNuovo(true)}>+ {labels.catalogo.sing}</button>
        </div>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        {righe.length === 0
          ? <div className="pw-empty">Nessun/a «{labels.catalogo.sing}». Aggiungine uno o importa un CSV.</div>
          : (
            <div style={{ overflowX: 'auto' }}>
              <table className="pw-table">
                <thead><tr>
                  <th>Nome</th><th>Marca</th><th>Categoria</th><th>U. vendita</th>
                  <th style={{ textAlign: 'right' }}>Prezzo</th><th style={{ textAlign: 'right' }}>IVA</th><th>Codice</th><th></th>
                </tr></thead>
                <tbody>
                  {righe.map(r => (
                    <tr key={r.id}>
                      <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{r.name}</td>
                      <td>{r.brand || '—'}</td>
                      <td style={{ fontSize: 13 }}>{catLabel(r.category_path)}</td>
                      <td style={{ fontSize: 13 }}>{r.unit_of_sale || '—'}</td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{euro(r.price)}</td>
                      <td style={{ textAlign: 'right' }}>{r.vat_rate != null && r.vat_rate !== '' ? `${Number(r.vat_rate)}%` : '—'}</td>
                      <td style={{ fontSize: 12, color: 'var(--fg-3)' }}>{r.sku || '—'}</td>
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
      {importa && <ImportCSV aziendaId={aziendaId} labels={labels}
        onClose={() => setImporta(false)} onDone={() => { setImporta(false); carica() }} />}
    </div>
  )
}

// ---------- Form manuale ----------
function EditItem({ item, aziendaId, labelSing, onClose, onSaved }: {
  item: Item | null; aziendaId: number | null; labelSing: string; onClose: () => void; onSaved: () => void
}) {
  const cat = parseArr(item?.category_path)
  const attr = (() => { try { return item?.attributes ? JSON.parse(item.attributes) : {} } catch { return {} } })()
  const [f, setF] = useState({
    name: item?.name || '', brand: item?.brand || '', sku: item?.sku || '',
    famiglia: cat[0] || '', sottofamiglia: cat[1] || '',
    unit_of_sale: item?.unit_of_sale || '', price: item?.price != null ? String(item.price) : '',
    vat_rate: item?.vat_rate != null ? String(item.vat_rate) : '',
    aliases: parseArr(item?.aliases).join('; '), description: item?.description || '',
    formato: attr.formato || '', pezzi_per_confezione: attr.pezzi_per_confezione || '', prezzo_confezione: attr.prezzo_confezione || '',
  })
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null)
  const set = (k: string, v: string) => setF(o => ({ ...o, [k]: v }))

  async function salva() {
    if (!f.name.trim()) { setErr('Il nome è obbligatorio.'); return }
    setBusy(true); setErr(null)
    const attributes: any = {}
    for (const k of ['formato', 'pezzi_per_confezione', 'prezzo_confezione'] as const)
      if ((f[k] || '').trim()) attributes[k] = f[k].trim()
    const catPath = [f.famiglia.trim(), f.sottofamiglia.trim()].filter(Boolean)
    const aliasesArr = f.aliases.split(/[;,]/).map(s => s.trim()).filter(Boolean)
    const payload: any = {
      name: f.name.trim(), brand: f.brand.trim() || null, sku: f.sku.trim() || null,
      unit_of_sale: f.unit_of_sale.trim() || null, price: num(f.price), vat_rate: num(f.vat_rate),
      description: f.description.trim() || null,
      aliases: aliasesArr.length ? JSON.stringify(aliasesArr) : null,
      category_path: catPath.length ? JSON.stringify(catPath) : null,
      attributes: Object.keys(attributes).length ? JSON.stringify(attributes) : null,
    }
    const res = item
      ? await supabase.from('catalog_items').update(payload).eq('id', item.id)
      : await supabase.from('catalog_items').insert({ ...payload, azienda_id: aziendaId, item_type: 'PRODUCT' })
    setBusy(false)
    if (res.error) setErr(res.error.message); else onSaved()
  }

  const F = (k: string, label: string, extra: any = {}) => (
    <div className="pw-field" style={{ flex: 1, minWidth: 140 }}><label>{label}</label>
      <input className="pw-input" value={(f as any)[k]} onChange={e => set(k, e.target.value)} {...extra} /></div>
  )
  return (
    <Modal title={item ? `Modifica ${labelSing}` : `Nuovo ${labelSing}`} width={640} onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button>
               <button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{busy ? 'Salvo…' : 'Salva'}</button></>}>
      <div className="pw-field"><label>Nome *</label><input className="pw-input" value={f.name} onChange={e => set('name', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>{F('brand', 'Marca')}{F('sku', 'Codice / EAN')}</div>
      <div className="pw-row" style={{ gap: 12 }}>{F('famiglia', 'Famiglia')}{F('sottofamiglia', 'Sottofamiglia')}</div>
      <div className="pw-row" style={{ gap: 12 }}>
        {F('unit_of_sale', 'Unità di vendita')}
        {F('price', 'Prezzo unitario (€)', { type: 'number', step: '0.01', min: '0' })}
        {F('vat_rate', 'IVA (%)', { type: 'number', step: '0.1', min: '0' })}
      </div>
      <div className="pw-field"><label>Alias (sinonimi, separati da ;)</label>
        <input className="pw-input" placeholder="es. moretti piccola; moretti" value={f.aliases} onChange={e => set('aliases', e.target.value)} /></div>
      <div className="pw-row" style={{ gap: 12 }}>{F('formato', 'Formato')}{F('pezzi_per_confezione', 'Pezzi/confezione')}{F('prezzo_confezione', 'Prezzo confezione (€)')}</div>
      <div className="pw-field"><label>Descrizione</label>
        <textarea className="pw-input" rows={2} style={{ resize: 'vertical', fontFamily: 'inherit' }} value={f.description} onChange={e => set('description', e.target.value)} /></div>
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}

// ---------- Import CSV con mappatura ----------
type Kind = 'text' | 'num' | 'aliases' | 'cat0' | 'cat1' | 'attr'
type Target = { key: string; label: string; kind: Kind; hints: string[] }
const TARGETS: Target[] = [
  { key: 'name', label: 'Nome *', kind: 'text', hints: ['descrizione', 'nome', 'name', 'prodotto'] },
  { key: 'brand', label: 'Marca', kind: 'text', hints: ['marca', 'brand'] },
  { key: 'sku', label: 'Codice / EAN', kind: 'text', hints: ['ean', 'sku', 'barcode', 'codice', 'id_prodotto'] },
  { key: 'famiglia', label: 'Famiglia', kind: 'cat0', hints: ['famiglia', 'family'] },
  { key: 'sottofamiglia', label: 'Sottofamiglia', kind: 'cat1', hints: ['sottofamiglia', 'subfamily'] },
  { key: 'unit_of_sale', label: 'Unità di vendita', kind: 'text', hints: ['unita_vendita', 'unita', 'unit'] },
  { key: 'price', label: 'Prezzo unitario', kind: 'num', hints: ['prezzo_unitario', 'prezzo', 'price'] },
  { key: 'vat_rate', label: 'Aliquota IVA', kind: 'num', hints: ['aliquota_iva', 'iva', 'vat'] },
  { key: 'aliases', label: 'Alias', kind: 'aliases', hints: ['alias', 'sinonimi'] },
  { key: 'description', label: 'Descrizione (lunga)', kind: 'text', hints: ['descrizione_lunga', 'note'] },
  { key: 'formato', label: 'Formato', kind: 'attr', hints: ['formato'] },
  { key: 'pezzi_per_confezione', label: 'Pezzi/confezione', kind: 'attr', hints: ['pezzi_per_confezione', 'pezzi'] },
  { key: 'prezzo_confezione', label: 'Prezzo confezione', kind: 'attr', hints: ['prezzo_confezione'] },
  { key: 'gradazione_alcolica', label: 'Gradazione', kind: 'attr', hints: ['gradazione_alcolica', 'gradazione'] },
  { key: 'giacenza', label: 'Giacenza', kind: 'attr', hints: ['giacenza', 'stock'] },
  { key: 'lotto_minimo', label: 'Lotto minimo', kind: 'attr', hints: ['lotto_minimo'] },
]

function parseCSV(text: string): { headers: string[]; rows: string[][] } {
  const lines = text.replace(/\r\n?/g, '\n').split('\n').filter(l => l.trim() !== '')
  if (!lines.length) return { headers: [], rows: [] }
  const parseLine = (line: string): string[] => {
    const out: string[] = []; let cur = ''; let q = false
    for (let i = 0; i < line.length; i++) {
      const c = line[i]
      if (q) {
        if (c === '"' && line[i + 1] === '"') { cur += '"'; i++ }
        else if (c === '"') q = false
        else cur += c
      } else if (c === '"') q = true
      else if (c === ',') { out.push(cur); cur = '' }
      else cur += c
    }
    out.push(cur)
    return out.map(s => s.trim())
  }
  return { headers: parseLine(lines[0]), rows: lines.slice(1).map(parseLine) }
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')

function ImportCSV({ aziendaId, labels, onClose, onDone }: {
  aziendaId: number | null; labels: CommercioLabels; onClose: () => void; onDone: () => void
}) {
  const [raw, setRaw] = useState('')
  const [headers, setHeaders] = useState<string[]>([])
  const [rows, setRows] = useState<string[][]>([])
  const [map, setMap] = useState<Record<string, number>>({})  // target.key -> header index (-1 = ignora)
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null)

  function analizza(text: string) {
    const { headers: h, rows: r } = parseCSV(text)
    if (!h.length) { setErr('CSV vuoto o non valido.'); return }
    setErr(null); setHeaders(h); setRows(r)
    // auto-guess: per ogni target, cerca l'header che matcha un hint (header non già usati)
    const used = new Set<number>(); const m: Record<string, number> = {}
    for (const t of TARGETS) {
      let idx = -1
      for (const hint of t.hints) {
        idx = h.findIndex((hd, i) => !used.has(i) && norm(hd) === norm(hint))
        if (idx >= 0) break
      }
      m[t.key] = idx
      if (idx >= 0) used.add(idx)
    }
    setMap(m)
  }

  const onFile = (file: File | undefined) => { if (file) file.text().then(t => { setRaw(t); analizza(t) }) }

  async function importa() {
    if (map.name == null || map.name < 0) { setErr('Mappa almeno il campo Nome.'); return }
    setBusy(true); setErr(null); setMsg(null)
    const records: any[] = []
    for (const row of rows) {
      const val = (k: string) => { const i = map[k]; return i != null && i >= 0 ? (row[i] ?? '').trim() : '' }
      const name = val('name'); if (!name) continue
      const attributes: any = {}
      for (const t of TARGETS) if (t.kind === 'attr' && val(t.key)) attributes[t.key] = val(t.key)
      const catPath = [val('famiglia'), val('sottofamiglia')].filter(Boolean)
      const aliasesArr = (val('aliases')).split(/[;,]/).map(s => s.trim()).filter(Boolean)
      records.push({
        azienda_id: aziendaId, item_type: 'PRODUCT', name,
        brand: val('brand') || null, sku: val('sku') || null,
        unit_of_sale: val('unit_of_sale') || null, price: num(val('price')), vat_rate: num(val('vat_rate')),
        description: val('description') || null,
        aliases: aliasesArr.length ? JSON.stringify(aliasesArr) : null,
        category_path: catPath.length ? JSON.stringify(catPath) : null,
        attributes: Object.keys(attributes).length ? JSON.stringify(attributes) : null,
      })
    }
    if (!records.length) { setBusy(false); setErr('Nessuna riga con un nome valido.'); return }
    // inserimento a blocchi di 500
    let inseriti = 0
    for (let i = 0; i < records.length; i += 500) {
      const { error } = await supabase.from('catalog_items').insert(records.slice(i, i + 500))
      if (error) { setBusy(false); setErr(`Errore all'inserimento: ${error.message}`); return }
      inseriti += Math.min(500, records.length - i)
    }
    setBusy(false); setMsg(`Importati ${inseriti} ${labels.catalogo.plur.toLowerCase()}.`)
    setTimeout(onDone, 900)
  }

  return (
    <Modal title={`Importa ${labels.catalogo.plur} da CSV`} width={720} onClose={onClose}
      footer={headers.length
        ? <><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button>
             <button className="pw-btn pw-btn-primary" disabled={busy} onClick={importa}>{busy ? 'Importo…' : `Importa ${rows.length} righe`}</button></>
        : <button className="pw-btn pw-btn-ghost" onClick={onClose}>Chiudi</button>}>
      {!headers.length ? (
        <div className="pw-stack" style={{ gap: 10 }}>
          <div className="pw-muted" style={{ fontSize: 13 }}>Carica un file CSV (prima riga = intestazioni) o incolla il contenuto.</div>
          <input type="file" accept=".csv,text/csv" onChange={e => onFile(e.target.files?.[0])} />
          <textarea className="pw-input" rows={5} placeholder="…oppure incolla qui il CSV" value={raw}
            style={{ resize: 'vertical', fontFamily: 'var(--font-mono)', fontSize: 12 }}
            onChange={e => setRaw(e.target.value)} />
          <button className="pw-btn pw-btn-ghost pw-btn-sm" style={{ alignSelf: 'flex-start' }}
            disabled={!raw.trim()} onClick={() => analizza(raw)}>Analizza CSV incollato</button>
        </div>
      ) : (
        <div className="pw-stack" style={{ gap: 10 }}>
          <div className="pw-muted" style={{ fontSize: 13 }}>
            {rows.length} righe. Associa ogni campo del catalogo a una colonna del CSV (le colonne non mappate vengono ignorate).
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {TARGETS.map(t => (
              <label key={t.key} className="pw-row" style={{ gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 13, color: t.key === 'name' ? 'var(--fg)' : 'var(--fg-2)' }}>{t.label}</span>
                <select className="pw-select pw-btn-sm" style={{ maxWidth: 160 }} value={map[t.key] ?? -1}
                  onChange={e => setMap(m => ({ ...m, [t.key]: Number(e.target.value) }))}>
                  <option value={-1}>— ignora —</option>
                  {headers.map((h, i) => <option key={i} value={i}>{h}</option>)}
                </select>
              </label>
            ))}
          </div>
          {err && <div className="pw-error">{err}</div>}
          {msg && <div className="pw-badge ok">{msg}</div>}
        </div>
      )}
      {!headers.length && err && <div className="pw-error" style={{ marginTop: 8 }}>{err}</div>}
    </Modal>
  )
}

// ---------- Rinomina etichette ----------
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
    ['catalogo', 'Catalogo (le cose che offri)'], ['ordine', 'Ordine (la richiesta del cliente)'], ['riga', 'Riga (voce dell\'ordine)'],
  ]
  return (
    <Modal title="Rinomina etichette (per questo cliente)" onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button>
               <button className="pw-btn pw-btn-primary" disabled={busy} onClick={salva}>{busy ? 'Salvo…' : 'Salva'}</button></>}>
      <div className="pw-muted" style={{ fontSize: 13 }}>
        Es. HORECA: {DEFAULT_LABELS.catalogo.plur}/{DEFAULT_LABELS.ordine.plur}; altri: «Servizi»/«Prenotazioni».
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
