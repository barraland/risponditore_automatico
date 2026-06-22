import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../lib/auth'

export default function Layout() {
  const { session, signOut } = useAuth()
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
          <NavLink to="/documenti">Documenti</NavLink>
        </div>
        <div className="pw-nav-right">
          <NavLink to="/assistente">Configurazione assistente</NavLink>
          <span className="pw-muted" style={{ fontSize: 13 }}>{session?.user?.email}</span>
          <button className="pw-btn pw-btn-ghost pw-btn-sm" onClick={() => signOut()}>Esci</button>
        </div>
      </nav>
      <main className="pw-container">
        <Outlet />
      </main>
    </>
  )
}
