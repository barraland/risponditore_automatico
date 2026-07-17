import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { supabase } from '../lib/supabase'
import { useTenant } from '../lib/tenant'
import { useCommercioLabels } from '../lib/commercioLabels'
import { dataOra, euro, nomeContatto, lower, badgeTicket } from '../lib/format'

type Persona = { id: number; nome: string | null; cognome: string | null } | null
type Chiamata = { id: number; telefono: string | null; riassunto: string | null; iniziata_at: string | null; contatti: Persona }
type Ordine = { id: number; total: number | string | null; status: string | null; created_at: string | null; contatti: Persona }
type Ticket = { id: number; titolo: string | null; stato: string | null; created_at: string | null; contatti: Persona }

const N0 = { contatti: 0, ordini: 0, bozze: 0, catalogo: 0, ticket: 0, chiamate: 0 }

// Riquadro numerico cliccabile: il numero è il dato, l'etichetta dice cos'è.
function Tile({ label, value, to, hint }: { label: string; value: number; to: string; hint?: string }) {
  return (
    <Link to={to} title={hint || label} style={{ textDecoration: 'none' }}>
      <div className="pw-card" style={{ padding: '14px 16px', height: '100%' }}>
        <div style={{ fontSize: 30, fontWeight: 700, color: 'var(--fg)', lineHeight: 1.1 }}>{value}</div>
        <div className="pw-muted" style={{ fontSize: 13, marginTop: 4 }}>{label}</div>
        {hint && <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2 }}>{hint}</div>}
      </div>
    </Link>
  )
}

function Card({ titolo, azione, children }: { titolo: string; azione?: ReactNode; children: ReactNode }) {
  return (
    <div className="pw-card">
      <div className="pw-between" style={{ marginBottom: 10 }}>
        <h3 style={{ margin: 0, fontSize: 16 }}>{titolo}</h3>
        {azione}
      </div>
      {children}
    </div>
  )
}

export default function Home() {
  const { aziendaId, aziende } = useTenant()
  const attiva = aziende.find(a => a.id === aziendaId)
  const [labels] = useCommercioLabels(aziendaId)
  const mostraOrdini = (attiva as any)?.mostra_ordini !== false

  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [n, setN] = useState(N0)
  const [chiamate, setChiamate] = useState<Chiamata[]>([])
  const [ordini, setOrdini] = useState<Ordine[]>([])
  const [ticket, setTicket] = useState<Ticket[]>([])

  useEffect(() => {
    if (!aziendaId) { setLoading(false); return }
    let vivo = true
    setLoading(true); setErr(null)

    // Conteggio senza scaricare le righe (head + count esatto).
    const quanti = async (tabella: string, filtro?: [string, string]) => {
      let q = supabase.from(tabella).select('*', { count: 'exact', head: true }).eq('azienda_id', aziendaId)
      if (filtro) q = q.eq(filtro[0], filtro[1])
      const { count } = await q
      return count || 0
    }

    Promise.all([
      quanti('contatti'),
      quanti('orders'),
      quanti('orders', ['status', 'DRAFT']),
      quanti('catalog_items'),
      quanti('ticket', ['stato', 'APERTO']),
      quanti('chiamate_voce'),
      supabase.from('chiamate_voce').select('id, telefono, riassunto, iniziata_at, contatti(id, nome, cognome)')
        .eq('azienda_id', aziendaId).order('iniziata_at', { ascending: false }).limit(5),
      supabase.from('orders').select('id, total, status, created_at, contatti(id, nome, cognome)')
        .eq('azienda_id', aziendaId).order('created_at', { ascending: false }).limit(5),
      supabase.from('ticket').select('id, titolo, stato, created_at, contatti(id, nome, cognome)')
        .eq('azienda_id', aziendaId).eq('stato', 'APERTO').order('created_at', { ascending: false }).limit(5),
    ]).then(([contatti, ord, bozze, catalogo, tk, ch, ultimeCh, ultimiOr, ultimiTk]: any[]) => {
      if (!vivo) return
      setN({ contatti, ordini: ord, bozze, catalogo, ticket: tk, chiamate: ch })
      setChiamate(ultimeCh?.data || [])
      setOrdini(ultimiOr?.data || [])
      setTicket(ultimiTk?.data || [])
      setLoading(false)
    }).catch((e: any) => { if (vivo) { setErr(e?.message || 'Errore nel caricamento'); setLoading(false) } })

    return () => { vivo = false }
  }, [aziendaId])

  if (loading) return <div className="pw-spinner">Caricamento…</div>

  return (
    <div className="pw-stack">
      <div>
        <div className="pw-eyebrow">Centralino AI</div>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>{attiva?.nome || 'Dashboard'}</h1>
        <p className="pw-muted" style={{ maxWidth: 720, marginTop: 6 }}>
          L'assistente risponde al telefono e su WhatsApp, riconosce il cliente, consulta la base di
          conoscenza e usa gli strumenti configurati. Qui vedi cosa ha fatto e da qui configuri tutto.
        </p>
      </div>

      {err && <div className="pw-error">{err}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
        <Tile label="Contatti" value={n.contatti} to="/contatti" />
        <Tile label="Chiamate" value={n.chiamate} to="/ticket" hint="con trascrizione" />
        <Tile label="Ticket aperti" value={n.ticket} to="/ticket" />
        {mostraOrdini && (
          <Tile label={labels.ordine.plur} value={n.ordini} to="/ordini"
            hint={n.bozze ? `${n.bozze} da confermare` : undefined} />
        )}
        {mostraOrdini && <Tile label={labels.catalogo.plur} value={n.catalogo} to="/catalogo" />}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 16 }}>
        <Card titolo="Ultime chiamate" azione={<Link className="pw-btn pw-btn-ghost pw-btn-sm" to="/ticket">Vedi tutte</Link>}>
          {chiamate.length === 0
            ? <div className="pw-empty">Nessuna chiamata registrata.</div>
            : (
              <div className="pw-stack" style={{ gap: 10 }}>
                {chiamate.map(c => (
                  <div key={c.id} style={{ borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
                    <div className="pw-between" style={{ gap: 8 }}>
                      <span style={{ fontWeight: 600, color: 'var(--fg)' }}>
                        {c.contatti ? <Link to={`/contatti/${c.contatti.id}`}>{nomeContatto(c.contatti)}</Link> : (c.telefono || '—')}
                      </span>
                      <span className="pw-muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{dataOra(c.iniziata_at)}</span>
                    </div>
                    {c.riassunto && (
                      <div className="pw-muted" style={{ fontSize: 13, marginTop: 3 }}>
                        {c.riassunto.length > 160 ? c.riassunto.slice(0, 160) + '…' : c.riassunto}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
        </Card>

        {mostraOrdini ? (
          <Card titolo={`Ultimi ${labels.ordine.plur.toLowerCase()}`} azione={<Link className="pw-btn pw-btn-ghost pw-btn-sm" to="/ordini">Vedi tutti</Link>}>
            {ordini.length === 0
              ? <div className="pw-empty">Nessun/a «{labels.ordine.sing}» ancora.</div>
              : (
                <table className="pw-table">
                  <tbody>
                    {ordini.map(o => (
                      <tr key={o.id} style={{ cursor: 'default' }}>
                        <td style={{ fontWeight: 600, color: 'var(--fg)' }}>
                          {o.contatti ? <Link to={`/contatti/${o.contatti.id}`}>{nomeContatto(o.contatti)}</Link> : '—'}
                        </td>
                        <td className="pw-muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{dataOra(o.created_at)}</td>
                        <td><span className="pw-badge mute">{lower(o.status)}</span></td>
                        <td style={{ textAlign: 'right', fontWeight: 600, color: 'var(--fg)', whiteSpace: 'nowrap' }}>{euro(Number(o.total))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </Card>
        ) : (
          <Card titolo="Ticket aperti" azione={<Link className="pw-btn pw-btn-ghost pw-btn-sm" to="/ticket">Vedi tutti</Link>}>
            {ticket.length === 0
              ? <div className="pw-empty">Nessun ticket aperto.</div>
              : (
                <table className="pw-table">
                  <tbody>
                    {ticket.map(t => (
                      <tr key={t.id} style={{ cursor: 'default' }}>
                        <td style={{ color: 'var(--fg)' }}>{t.titolo || '—'}</td>
                        <td className="pw-muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{dataOra(t.created_at)}</td>
                        <td><span className={`pw-badge ${badgeTicket(t.stato)}`}>{lower(t.stato)}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </Card>
        )}
      </div>

      <Card titolo="Configurazione dell'assistente">
        <div className="pw-row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <Link className="pw-btn pw-btn-ghost pw-btn-sm" to="/prompt">System prompt</Link>
          <Link className="pw-btn pw-btn-ghost pw-btn-sm" to="/documenti">Base di Conoscenza</Link>
          <Link className="pw-btn pw-btn-ghost pw-btn-sm" to="/inoltri">Inoltra &amp; Admin</Link>
          <Link className="pw-btn pw-btn-ghost pw-btn-sm" to="/entita">Configurazione Entità</Link>
          <Link className="pw-btn pw-btn-ghost pw-btn-sm" to="/tools">MCP server</Link>
          <Link className="pw-btn pw-btn-ghost pw-btn-sm" to="/promemoria">Promemoria</Link>
        </div>
      </Card>
    </div>
  )
}
