import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'
import { dataOra, nomeContatto } from '../lib/format'
import Modal from '../components/Modal'

type Campo = { chiave: string; label: string; tipo: string; obbligatorio: boolean; opzioni: string[] }
type Tipo = { id: number; nome_singolare: string; nome_plurale: string; campo_etichetta: string; campi: Campo[] }

function etichettaDa(tipo: Tipo, valori: any): string {
  if (tipo.campo_etichetta && valori?.[tipo.campo_etichetta]) return String(valori[tipo.campo_etichetta])
  for (const c of tipo.campi) if (valori?.[c.chiave]) return String(valori[c.chiave])
  return `${tipo.nome_singolare} #—`
}

export default function EntitaLista() {
  const { aziendaId } = useTenant()
  const [tipo, setTipo] = useState<Tipo | null>(null)
  const [righe, setRighe] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [edit, setEdit] = useState<any | null>(null)
  const [view, setView] = useState<any | null>(null)
  const [params] = useSearchParams()

  // Arrivando da un contatto con ?open=<id>, apre quell'entità in VISUALIZZAZIONE (non in modifica).
  useEffect(() => {
    const openId = params.get('open')
    if (openId && righe.length) {
      const r = righe.find(x => String(x.id) === openId)
      if (r) setView(r)
    }
  }, [righe])   // eslint-disable-line react-hooks/exhaustive-deps

  async function carica() {
    if (!aziendaId) { setLoading(false); return }
    setLoading(true); setErr(null)
    const { data: tt, error: e1 } = await supabase.from('entita_tipo')
      .select('*').eq('azienda_id', aziendaId).eq('attivo', true).order('id', { ascending: false }).limit(1)
    if (e1) { setErr(e1.message); setLoading(false); return }
    const t = (tt || [])[0]
    if (!t) { setTipo(null); setRighe([]); setLoading(false); return }
    const tipoObj: Tipo = {
      id: t.id, nome_singolare: t.nome_singolare, nome_plurale: t.nome_plurale || t.nome_singolare,
      campo_etichetta: t.campo_etichetta || '', campi: (() => { try { return JSON.parse(t.campi || '[]') } catch { return [] } })(),
    }
    setTipo(tipoObj)
    const { data, error } = await supabase.from('entita')
      .select('id, etichetta, valori, created_at, contatto_entita(contatti(id, nome, cognome))')
      .eq('azienda_id', aziendaId).eq('tipo_id', t.id).order('id', { ascending: false })
    if (error) setErr(error.message)
    else setRighe((data || []).map((r: any) => ({ ...r, valoriObj: (() => { try { return JSON.parse(r.valori || '{}') } catch { return {} } })() })))
    setLoading(false)
  }
  useEffect(() => { carica() }, [aziendaId])

  async function elimina(r: any) {
    if (!confirm(`Eliminare «${r.etichetta || etichettaDa(tipo!, r.valoriObj)}»?`)) return
    const { error } = await supabase.from('entita').delete().eq('id', r.id)
    if (error) setErr(error.message); else carica()
  }

  if (loading) return <div className="pw-spinner">Caricamento…</div>

  if (!tipo) {
    return (
      <div className="pw-stack" style={{ maxWidth: 720 }}>
        <h1 style={{ fontSize: 28 }}>Entità</h1>
        <div className="pw-card"><div className="pw-card-body pw-muted">
          Nessun tipo di entità <strong>attivo</strong>. Vai in <Link to="/entita">Entità (configurazione)</Link> e attivane uno.
        </div></div>
      </div>
    )
  }

  // Il campo-etichetta è già la 1ª colonna "Nome": escludilo dalle altre per non duplicarlo.
  const campoEt = tipo.campi.find(c => c.chiave === tipo.campo_etichetta)
  const colonne = tipo.campi.filter(c => c.chiave !== tipo.campo_etichetta).slice(0, 3)

  return (
    <div className="pw-stack">
      <div className="pw-between" style={{ flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div className="pw-eyebrow">CRM</div>
          <h1 style={{ fontSize: 28, marginTop: 6 }}>{tipo.nome_plurale}</h1>
          <div className="pw-muted" style={{ fontSize: 14, marginTop: 6 }}>
            I record «{tipo.nome_singolare}» collegati ai contatti. Li registra l'assistente durante le
            conversazioni; qui li vedi, modifichi ed elimini.
          </div>
        </div>
        <Link to="/entita" className="pw-btn pw-btn-ghost pw-btn-sm">⚙️ Configura tipo</Link>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div className="pw-card">
        {righe.length === 0
          ? <div className="pw-empty">Nessun/a «{tipo.nome_singolare}» ancora registrato/a.</div>
          : (
            <div style={{ overflowX: 'auto' }}>
              <table className="pw-table">
                <thead><tr>
                  <th>{campoEt?.label || 'Nome'}</th>
                  {colonne.map(c => <th key={c.chiave}>{c.label}</th>)}
                  <th>Contatti</th><th>Creato</th><th></th>
                </tr></thead>
                <tbody>
                  {righe.map(r => (
                    <tr key={r.id}>
                      <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{r.etichetta || etichettaDa(tipo, r.valoriObj)}</td>
                      {colonne.map(c => <td key={c.chiave}>{String(r.valoriObj?.[c.chiave] ?? '—')}</td>)}
                      <td>{(r.contatto_entita || []).map((l: any) => l.contatti).filter(Boolean).map((c: any, i: number) => (
                        <span key={c.id}>{i > 0 ? ', ' : ''}<Link to={`/contatti/${c.id}`}>{nomeContatto(c)}</Link></span>
                      )) || '—'}</td>
                      <td>{dataOra(r.created_at)}</td>
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

      {view && <ViewIstanza tipo={tipo} riga={view} onClose={() => setView(null)}
        onEdit={() => { setEdit(view); setView(null) }} />}
      {edit && <EditIstanza tipo={tipo} riga={edit} onClose={() => setEdit(null)}
        onSaved={() => { setEdit(null); carica() }} />}
    </div>
  )
}

function ViewIstanza({ tipo, riga, onClose, onEdit }: {
  tipo: Tipo; riga: any; onClose: () => void; onEdit: () => void
}) {
  const v = riga.valoriObj || {}
  const contatti = (riga.contatto_entita || []).map((l: any) => l.contatti).filter(Boolean)
  return (
    <Modal title={riga.etichetta || etichettaDa(tipo, v)} width={620} onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Chiudi</button>
               <button className="pw-btn pw-btn-primary" onClick={onEdit}>Modifica</button></>}>
      {tipo.campi.map(c => (
        <div key={c.chiave} className="pw-field">
          <label>{c.label}</label>
          <div style={{ padding: '4px 0', fontSize: 15, color: 'var(--fg)' }}>
            {v[c.chiave] != null && v[c.chiave] !== '' ? String(v[c.chiave]) : '—'}
          </div>
        </div>
      ))}
      <div className="pw-field">
        <label>Contatti collegati</label>
        <div style={{ padding: '4px 0' }}>
          {contatti.length
            ? contatti.map((c: any, i: number) => (
              <span key={c.id}>{i > 0 ? ', ' : ''}<Link to={`/contatti/${c.id}`}>{nomeContatto(c)}</Link></span>))
            : '—'}
        </div>
      </div>
    </Modal>
  )
}

function EditIstanza({ tipo, riga, onClose, onSaved }: {
  tipo: Tipo; riga: any; onClose: () => void; onSaved: () => void
}) {
  const [valori, setValori] = useState<any>({ ...riga.valoriObj })
  const [err, setErr] = useState<string | null>(null)

  function set(k: string, v: any) { setValori((o: any) => ({ ...o, [k]: v })) }

  async function salva() {
    const v = Object.fromEntries(Object.entries(valori).filter(([, x]) => x !== '' && x != null))
    const etichetta = etichettaDa(tipo, v)
    const { error } = await supabase.from('entita').update({ valori: JSON.stringify(v), etichetta }).eq('id', riga.id)
    if (error) setErr(error.message); else onSaved()
  }

  return (
    <Modal title={`Modifica ${tipo.nome_singolare}`} width={620} onClose={onClose}
      footer={<><button className="pw-btn pw-btn-ghost" onClick={onClose}>Annulla</button>
               <button className="pw-btn pw-btn-primary" onClick={salva}>Salva</button></>}>
      {err && <div className="pw-error">{err}</div>}
      {tipo.campi.map(c => (
        <div key={c.chiave} className="pw-field">
          <label>{c.label}{c.obbligatorio ? ' *' : ''}</label>
          {c.tipo === 'scelta'
            ? <select className="pw-select" value={valori[c.chiave] ?? ''} onChange={e => set(c.chiave, e.target.value)}>
                <option value="">—</option>
                {(c.opzioni || []).map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            : <input className="pw-input" type={c.tipo === 'numero' ? 'number' : c.tipo === 'data' ? 'date' : 'text'}
                value={valori[c.chiave] ?? ''} onChange={e => set(c.chiave, e.target.value)} />}
        </div>
      ))}
    </Modal>
  )
}
