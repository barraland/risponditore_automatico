import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { supabase } from './supabase'
import { useAuth } from './auth'

export type Azienda = { id: number; nome: string }

type TenantCtx = {
  ready: boolean
  isSuperAdmin: boolean
  aziende: Azienda[]
  aziendaId: number | null
  setAziendaId: (id: number) => void
  reload: () => Promise<void>
}

const Ctx = createContext<TenantCtx>(null as any)
const LS_KEY = 'pw_tenant'

export function TenantProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth()
  const [ready, setReady] = useState(false)
  const [isSuperAdmin, setSuper] = useState(false)
  const [aziende, setAziende] = useState<Azienda[]>([])
  const [aziendaId, setAid] = useState<number | null>(null)

  async function load() {
    if (!session) { setReady(false); setAziende([]); setAid(null); return }
    setReady(false)
    // Sono super-admin? (RLS: leggo solo la mia riga)
    const { data: sa } = await supabase
      .from('super_admin').select('user_id').eq('user_id', session.user.id).maybeSingle()
    setSuper(!!sa)
    // Elenco tenant visibili — RLS: super-admin li vede tutti, il cliente solo il suo.
    const { data: az } = await supabase.from('azienda').select('id, nome').order('id')
    const lista = (az || []) as Azienda[]
    setAziende(lista)
    // Tenant attivo: ultimo scelto (se ancora visibile), altrimenti il primo.
    const saved = Number(localStorage.getItem(LS_KEY) || '')
    const scelto = lista.find(a => a.id === saved)?.id ?? lista[0]?.id ?? null
    setAid(scelto)
    setReady(true)
  }

  useEffect(() => { load() }, [session?.user?.id])

  function setAziendaId(id: number) {
    localStorage.setItem(LS_KEY, String(id))
    setAid(id)
  }

  return (
    <Ctx.Provider value={{ ready, isSuperAdmin, aziende, aziendaId, setAziendaId, reload: load }}>
      {children}
    </Ctx.Provider>
  )
}

export const useTenant = () => useContext(Ctx)
