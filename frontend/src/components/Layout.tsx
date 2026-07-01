import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../lib/auth'
import { useTenant } from '../lib/tenant'

function TenantSwitcher() {
  const { isSuperAdmin, aziende, aziendaId, setAziendaId } = useTenant()
  const attiva = aziende.find(a => a.id === aziendaId)
  if (!isSuperAdmin) {
    // Utente-cliente: un solo tenant, mostrato come etichetta statica.
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

export default function Layout() {
  const { session, signOut } = useAuth()
  const { isSuperAdmin, aziendaId } = useTenant()
  return (
    <>
      <nav className="pw-nav">
        <a href="https://pipework.it/" target="_blank" rel="noreferrer" className="pw-brand">
          <img src="/pipework-mark.svg" alt="Pipework" /> Pipework
        </a>
        <div className="pw-nav-links">
          <NavLink to="/societa">Società</NavLink>
          <NavLink to="/ordini">Ordini</NavLink>
          <NavLink to="/agenti">Agenti</NavLink>
          <NavLink to="/contatti">Contatti</NavLink>
          <NavLink to="/ticket">Ticket</NavLink>
          <NavLink to="/documenti">Documenti</NavLink>
          <NavLink to="/promemoria">Promemoria</NavLink>
          <NavLink to="/inoltri">Inoltri</NavLink>
          <NavLink to="/calendario">Calendario</NavLink>
        </div>
        <div className="pw-nav-right">
          <TenantSwitcher />
          {isSuperAdmin && <NavLink to="/clienti">Clienti</NavLink>}
          <NavLink to="/admin">Admin</NavLink>
          <NavLink to="/assistente">Configurazione assistente</NavLink>
          <span className="pw-muted" style={{ fontSize: 13 }}>{session?.user?.email}</span>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => signOut()}>Esci</button>
        </div>
      </nav>
      <main className="pw-container">
        {/* key sul tenant: cambiando cliente le pagine si rimontano e ricaricano i dati filtrati. */}
        <Outlet key={aziendaId ?? 'none'} />
      </main>
    </>
  )
}
