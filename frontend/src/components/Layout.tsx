import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { useTenant } from '../lib/tenant'
import { supabase } from '../lib/supabase'
import Modal from './Modal'
import GoogleConnect from './GoogleConnect'

function TenantSwitcher() {
  const { isSuperAdmin, aziende, aziendaId, setAziendaId } = useTenant()
  const attiva = aziende.find(a => a.id === aziendaId)
  if (!isSuperAdmin) {
    if (!attiva) return null
    return <span className="pw-tenant-tag" title="Il tuo spazio cliente">{attiva.nome}</span>
  }
  return (
    <select
      className="pw-input pw-btn-sm"
      style={{ maxWidth: 220 }}
      value={aziendaId ?? ''}
      onChange={e => setAziendaId(Number(e.target.value))}
      title="Cliente attivo (super-admin)"
    >
      {aziende.map(a => <option key={a.id} value={a.id}>{a.nome}</option>)}
    </select>
  )
}

// Pannello ⚙️: mostra/nascondi voci di menù per il cliente attivo (salva su azienda).
function GuiConfig({ onClose }: { onClose: () => void }) {
  const { aziendaId, aziende, reload } = useTenant()
  const attiva = aziende.find(a => a.id === aziendaId)
  const [err, setErr] = useState<string | null>(null)
  const VOCI: [string, string][] = [
    ['mostra_ordini', 'Ordini'], ['mostra_agenti', 'Agenti'], ['mostra_calendario', 'Calendario'],
  ]
  async function toggle(campo: string, mostra: boolean) {
    if (!aziendaId) return
    // salviamo false per nascondere; null quando è mostrato (default)
    const { error } = await supabase.from('azienda').update({ [campo]: mostra ? null : false }).eq('id', aziendaId)
    if (error) setErr(error.message); else { setErr(null); await reload() }
  }
  return (
    <Modal title={`Personalizza dashboard — ${attiva?.nome ?? ''}`} onClose={onClose}>
      <div className="pw-muted" style={{ fontSize: 13 }}>
        Nascondi le voci di menù non pertinenti a questo cliente. Non elimina dati: nasconde solo i pulsanti.
      </div>
      {VOCI.map(([campo, label]) => {
        const mostra = (attiva as any)?.[campo] !== false
        return (
          <label key={campo} className="pw-between" style={{ cursor: 'pointer', padding: '6px 0' }}>
            <span>{label}</span>
            <input type="checkbox" checked={mostra} onChange={e => toggle(campo, e.target.checked)} />
          </label>
        )
      })}
      {err && <div className="pw-error">{err}</div>}
    </Modal>
  )
}

export default function Layout() {
  const { session, signOut } = useAuth()
  const { isSuperAdmin, aziendaId, aziende } = useTenant()
  const [config, setConfig] = useState(false)
  const [google, setGoogle] = useState(false)
  const attiva = aziende.find(a => a.id === aziendaId)
  const mostra = (campo: string) => (attiva as any)?.[campo] !== false  // default: visibile
  return (
    <>
      <nav className="pw-nav">
        <a href="https://pipework.it/" target="_blank" rel="noreferrer" className="pw-brand">
          <img src="/pipework-mark.svg" alt="Pipework" /> Pipework
        </a>
        <div className="pw-nav-links">
          <NavLink to="/contatti">Contatti</NavLink>
          <NavLink to="/societa">Società</NavLink>
          {mostra('mostra_agenti') && <NavLink to="/agenti">Agenti</NavLink>}
          {mostra('mostra_ordini') && <NavLink to="/ordini">Ordini</NavLink>}
          <NavLink to="/ticket">Ticket</NavLink>
          <NavLink to="/documenti">Base di Conoscenza</NavLink>
          <NavLink to="/promemoria">Promemoria</NavLink>
          <NavLink to="/inoltri">Inoltri</NavLink>
          {mostra('mostra_calendario') && <NavLink to="/calendario">Calendario</NavLink>}
        </div>
        <div className="pw-nav-right">
          <TenantSwitcher />
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setGoogle(true)}
            title="Collega l'account Google del cliente (Calendar + invio email)">Google account</button>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" title="Personalizza dashboard"
            onClick={() => setConfig(true)} style={{ fontSize: 16, lineHeight: 1 }}>⚙️</button>
          {isSuperAdmin && <NavLink to="/clienti">Clienti</NavLink>}
          <NavLink to="/admin">Admin</NavLink>
          <NavLink to="/prompt">Assistente</NavLink>
          <span className="pw-muted" style={{ fontSize: 13 }}>{session?.user?.email}</span>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => signOut()}>Esci</button>
        </div>
      </nav>
      <main className="pw-container">
        <Outlet key={aziendaId ?? 'none'} />
      </main>
      {config && <GuiConfig onClose={() => setConfig(false)} />}
      {google && (
        <Modal title="Google account del cliente" onClose={() => setGoogle(false)}>
          <div className="pw-muted" style={{ fontSize: 13 }}>
            Collega l'account Google del cliente per prenotare meeting sul suo calendario e inviare
            le email <strong>dalla sua casella</strong>. È facoltativo.
          </div>
          <GoogleConnect bare />
        </Modal>
      )}
    </>
  )
}
