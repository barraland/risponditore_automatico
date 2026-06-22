// Gli enum sono salvati in MAIUSCOLO dal backend (CLIENTE, RISTORANTE...). Qui li
// rendiamo leggibili per la UI.

export const lower = (s?: string | null) => (s || '').toLowerCase()

export function euro(n?: number | null) {
  return `€ ${(n ?? 0).toLocaleString('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function dataBreve(iso?: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleDateString('it-IT')
}

// Classe badge per lo stato relazione della società
export function badgeStato(stato?: string | null) {
  const s = lower(stato)
  if (s === 'cliente') return 'ok'
  if (s === 'inattivo') return 'mute'
  return 'warn' // prospect
}

// Classe badge per lo stato ordine
export function badgeOrdine(stato?: string | null) {
  const s = lower(stato)
  if (s === 'confermato') return 'ok'
  if (s === 'evaso') return 'cy'
  if (s === 'annullato') return 'mute'
  return 'warn' // bozza
}

// Opzioni per i <select> — value MAIUSCOLO (come salva il backend), label minuscola.
export const TIPI: [string, string][] = [
  ['RISTORANTE', 'ristorante'], ['PIZZERIA', 'pizzeria'], ['BAR', 'bar'],
  ['HOTEL', 'hotel'], ['GASTRONOMIA', 'gastronomia'], ['ALTRO', 'altro'],
]
export const STATI_REL: [string, string][] = [
  ['PROSPECT', 'prospect'], ['CLIENTE', 'cliente'], ['INATTIVO', 'inattivo'],
]
export const STATI_ORDINE: [string, string][] = [
  ['BOZZA', 'bozza'], ['CONFERMATO', 'confermato'], ['EVASO', 'evaso'], ['ANNULLATO', 'annullato'],
]
export const CANALI: [string, string][] = [
  ['MANUALE', 'manuale'], ['WHATSAPP', 'whatsapp'], ['VOCE', 'voce'], ['EMAIL', 'email'], ['AGENTE', 'agente'],
]
export const ORIGINI: [string, string][] = [['CLIENTE', 'cliente'], ['AGENTE', 'agente']]

export function nomeAgente(a?: { nome?: string | null; cognome?: string | null } | null) {
  if (!a) return null
  return `${a.nome || ''} ${a.cognome || ''}`.trim() || null
}
export function nomeContatto(c?: { nome?: string | null; cognome?: string | null } | null) {
  if (!c) return 'Senza nome'
  return `${c.nome || ''} ${c.cognome || ''}`.trim() || 'Senza nome'
}

// Documenti
export const DOC_CATEGORIE: [string, string][] = [
  ['listino', 'Listini e prezzi'],
  ['schede_prodotto', 'Schede prodotto/servizio'],
  ['contratti', 'Contratti e condizioni'],
  ['faq', 'FAQ e materiale informativo'],
  ['altro', 'Altri documenti'],
]
const DOC_CAT_LABEL = Object.fromEntries(DOC_CATEGORIE)
export const labelCategoria = (k?: string | null) => DOC_CAT_LABEL[k || ''] || k || '—'

export function badgeDoc(stato?: string | null) {
  const s = lower(stato)
  if (s === 'ready') return 'ok'
  if (s === 'processing') return 'warn'
  if (s === 'needs_review') return 'cy'
  return 'mute' // error
}

export function statoDoc(stato?: string | null) {
  const s = lower(stato)
  if (s === 'ready') return 'pronto'
  if (s === 'processing') return 'in elaborazione'
  if (s === 'needs_review') return 'da rivedere'
  if (s === 'error') return 'errore'
  return s
}

export function fileSize(bytes?: number | null) {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
