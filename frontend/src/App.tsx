import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './lib/auth'
import Layout from './components/Layout'
import Login from './pages/Login'
import SocietaList from './pages/SocietaList'
import SocietaDetail from './pages/SocietaDetail'
import OrdiniList from './pages/OrdiniList'
import OrdineDetail from './pages/OrdineDetail'
import AgentiList from './pages/AgentiList'
import AgenteDetail from './pages/AgenteDetail'
import ContattiList from './pages/ContattiList'
import ContattoDetail from './pages/ContattoDetail'
import Assistente from './pages/Assistente'
import Documenti from './pages/Documenti'
import DocumentoDetail from './pages/DocumentoDetail'
import TicketList from './pages/Ticket'

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
        <Route path="/ordini" element={<OrdiniList />} />
        <Route path="/ordini/:id" element={<OrdineDetail />} />
        <Route path="/agenti" element={<AgentiList />} />
        <Route path="/agenti/:id" element={<AgenteDetail />} />
        <Route path="/contatti" element={<ContattiList />} />
        <Route path="/contatti/:id" element={<ContattoDetail />} />
        <Route path="/ticket" element={<TicketList />} />
        <Route path="/documenti" element={<Documenti />} />
        <Route path="/documenti/:id" element={<DocumentoDetail />} />
        <Route path="/assistente" element={<Assistente />} />
      </Route>
      <Route path="*" element={<Navigate to="/societa" replace />} />
    </Routes>
  )
}
