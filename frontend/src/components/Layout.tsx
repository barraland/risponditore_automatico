import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { useTenant } from '../lib/tenant'
import { supabase } from '../lib/supabase'
import Modal from './Modal'
import GoogleConnect from './GoogleConnect'
import { DESIGN_SYSTEMS, getDesignSystem, setDesignSystem, type DesignSystem } from '../lib/designSystem'

// Selettore del DESIGN SYSTEM (aspetto della dashboard). Cambio immediato, persistito per-browser.
function DesignSystemSwitcher() {
  const [ds, setDs] = useState<DesignSystem>(getDesignSystem())
  return (
    <select className="pw-select pw-btn-sm" style={{ width: '100%' }} value={ds}
      title="Aspetto della dashboard (design system)"
      onChange={e => { const v = e.target.value as DesignSystem; setDs(v); setDesignSystem(v) }}>
      {DESIGN_SYSTEMS.map(d => <option key={d.id} value={d.id}>🎨 {d.nome}</option>)}
    </select>
  )
}

// Icona SVG (stile Feather, eredita currentColor).
function Ic({ children }: { children: ReactNode }) {
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
  )
}

const ICON: Record<string, ReactNode> = {
  contatti: <><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>,
  societa: <><path d="M3 21h18" /><path d="M5 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16" /><path d="M15 21V9h4a2 2 0 0 1 2 2v10" /><path d="M9 7h2M9 11h2M9 15h2" /></>,
  entita: <><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" /><polyline points="3.27 6.96 12 12.01 20.73 6.96" /><line x1="12" y1="22.08" x2="12" y2="12" /></>,
  agenti: <><rect x="2" y="7" width="20" height="14" rx="2" /><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" /></>,
  ordini: <><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z" /><path d="M3 6h18" /><path d="M16 10a4 4 0 0 1-8 0" /></>,
  ticket: <><path d="M2 9a3 3 0 0 0 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 0 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2z" /><path d="M13 5v2M13 11v2M13 17v2" /></>,
  documenti: <><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></>,
  promemoria: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" /></>,
  inoltri: <><polyline points="17 1 21 5 17 9" /><line x1="13" y1="5" x2="21" y2="5" /><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" /></>,
  calendario: <><rect x="3" y="4" width="18" height="18" rx="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></>,
  assistente: <><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8z" /></>,
  mcp: <><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" /></>,
  clienti: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /></>,
}

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
  const [menu, setMenu] = useState(false)   // drawer aperto (mobile)
  const [entTipo, setEntTipo] = useState<{ singolare: string; plurale: string } | null>(null)
  const attiva = aziende.find(a => a.id === aziendaId)
  const mostra = (campo: string) => (attiva as any)?.[campo] !== false  // default: visibile

  // Tipo-entità ATTIVO del tenant → diventa una voce di menù dinamica (es. "Animali", "Società").
  useEffect(() => {
    if (!aziendaId) { setEntTipo(null); return }
    supabase.from('entita_tipo').select('nome_singolare, nome_plurale')
      .eq('azienda_id', aziendaId).eq('attivo', true).order('id', { ascending: false }).limit(1)
      .then(({ data }) => {
        const t = (data || [])[0] as any
        setEntTipo(t ? { singolare: t.nome_singolare, plurale: t.nome_plurale || t.nome_singolare } : null)
      })
  }, [aziendaId])

  const VOCI: [string, string, boolean][] = [
    ['/ticket', 'Ticket', true],
    ['/documenti', 'Base di Conoscenza', true],
    ['/promemoria', 'Promemoria', true],
    ['/inoltri', 'Inoltra & Admin', true],
    ['/calendario', 'Calendario', mostra('mostra_calendario')],
  ]
  const key = (path: string) => path.replace('/', '')

  return (
    <div className="pw-app">
      <button className="pw-burger" onClick={() => setMenu(true)} aria-label="Apri menù">
        <Ic><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" /></Ic>
      </button>
      <div className={`pw-side-backdrop ${menu ? 'show' : ''}`} onClick={() => setMenu(false)} />

      <aside className={`pw-side ${menu ? 'open' : ''}`}>
        <a href="https://pipework.it/" target="_blank" rel="noreferrer" className="pw-brand">
          <img src="/pipework-mark.svg" alt="Pipework" /> Pipework
        </a>
        <nav className="pw-side-nav" onClick={() => setMenu(false)}>
          <NavLink to="/contatti"><Ic>{ICON.contatti}</Ic><span>Contatti</span></NavLink>
          {entTipo && <NavLink to="/entita-lista"><Ic>{ICON.entita}</Ic><span>{entTipo.plurale}</span></NavLink>}
          {VOCI.filter(([, , show]) => show).map(([to, label]) => (
            <NavLink key={to} to={to}><Ic>{ICON[key(to)]}</Ic><span>{label}</span></NavLink>
          ))}
          <div className="pw-side-sep" />
          <NavLink to="/entita"><Ic>{ICON.entita}</Ic><span>Configurazione Entità</span></NavLink>
          <NavLink to="/prompt"><Ic>{ICON.assistente}</Ic><span>System prompt</span></NavLink>
          <NavLink to="/tools"><Ic>{ICON.mcp}</Ic><span>MCP server</span></NavLink>
          {isSuperAdmin && <NavLink to="/clienti"><Ic>{ICON.clienti}</Ic><span>Seleziona Demo</span></NavLink>}
        </nav>

        <div className="pw-side-foot">
          <TenantSwitcher />
          <DesignSystemSwitcher />
          <button className="pw-btn pw-btn-ghost pw-btn-sm" title="Personalizza dashboard"
            onClick={() => setConfig(true)}>⚙️ Personalizza</button>
        </div>
      </aside>

      <div className="pw-main">
        <header className="pw-topbar">
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => setGoogle(true)}
            title="Collega l'account Google del cliente (Calendar + invio email)">🔗 Google account</button>
          <span className="pw-topbar-email" title={session?.user?.email || ''}>{session?.user?.email}</span>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => signOut()}>Esci</button>
        </header>
        <main className="pw-container">
          <Outlet key={aziendaId ?? 'none'} />
        </main>
      </div>

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
    </div>
  )
}
