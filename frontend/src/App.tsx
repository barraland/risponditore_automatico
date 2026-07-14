import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './lib/auth'
import Layout from './components/Layout'
import Login from './pages/Login'
import SocietaList from './pages/SocietaList'
import SocietaDetail from './pages/SocietaDetail'
import ContattiList from './pages/ContattiList'
import ContattoDetail from './pages/ContattoDetail'
import Admin from './pages/Admin'
import PromemoriaList from './pages/PromemoriaList'
import Inoltri from './pages/Inoltri'
import Calendario from './pages/Calendario'
import Documenti from './pages/Documenti'
import DocumentoDetail from './pages/DocumentoDetail'
import RetrieverTest from './pages/RetrieverTest'
import TicketList from './pages/Ticket'
import Clienti from './pages/Clienti'
import Prompt from './pages/Prompt'
import EntitaConfig from './pages/EntitaConfig'
import EntitaLista from './pages/EntitaLista'
import ToolsMCP from './pages/ToolsMCP'
import Catalogo from './pages/Catalogo'
import Ordini from './pages/Ordini'

function RequireAuth({ children }: { children: JSX.Element }) {
  const { session, loading } = useAuth()
  if (loading) return <div className="pw-spinner">Caricamento…</div>
  if (!session) return <Navigate to="/login" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route path="/" element={<Navigate to="/societa" replace />} />
        <Route path="/societa" element={<SocietaList />} />
        <Route path="/societa/:id" element={<SocietaDetail />} />
        <Route path="/entita" element={<EntitaConfig />} />
        <Route path="/entita-lista" element={<EntitaLista />} />
        <Route path="/contatti" element={<ContattiList />} />
        <Route path="/contatti/:id" element={<ContattoDetail />} />
        <Route path="/catalogo" element={<Catalogo />} />
        <Route path="/ordini" element={<Ordini />} />
        <Route path="/ticket" element={<TicketList />} />
        <Route path="/documenti" element={<Documenti />} />
        <Route path="/documenti/test" element={<RetrieverTest />} />
        <Route path="/documenti/:id" element={<DocumentoDetail />} />
        <Route path="/prompt" element={<Prompt />} />
        <Route path="/tools" element={<ToolsMCP />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/clienti" element={<Clienti />} />
        <Route path="/promemoria" element={<PromemoriaList />} />
        <Route path="/inoltri" element={<Inoltri />} />
        <Route path="/calendario" element={<Calendario />} />
      </Route>
      <Route path="*" element={<Navigate to="/societa" replace />} />
    </Routes>
  )
}
