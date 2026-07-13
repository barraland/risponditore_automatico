// Gestione del DESIGN SYSTEM attivo della SPA. I temi sono layer CSS (index.css = default
// "technical", themes/medicale.css = override attivato da <html data-ds="medicale">).
// La scelta è persistita in localStorage (per-browser): cambiarla è immediato, nessun rebuild.

export type DesignSystem = 'attuale' | 'medicale'

export const DESIGN_SYSTEMS: { id: DesignSystem; nome: string; descrizione: string }[] = [
  { id: 'medicale', nome: 'Medicale (bianco)', descrizione: 'Professionale, chiaro, moderno — teal clinico su bianco.' },
  { id: 'attuale', nome: 'Technical (scuro)', descrizione: 'Il tema originale: scuro, neon lime/cyan, monospace.' },
]

const KEY = 'pw-design-system'
const DEFAULT: DesignSystem = 'medicale'

export function getDesignSystem(): DesignSystem {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'attuale' || v === 'medicale') return v
  } catch { /* localStorage non disponibile */ }
  return DEFAULT
}

export function applicaDesignSystem(ds: DesignSystem = getDesignSystem()): void {
  // 'attuale' = tema di default (nessun attributo); gli altri si attivano via data-ds.
  const root = document.documentElement
  if (ds === 'attuale') root.removeAttribute('data-ds')
  else root.setAttribute('data-ds', ds)
}

export function setDesignSystem(ds: DesignSystem): void {
  try { localStorage.setItem(KEY, ds) } catch { /* ignore */ }
  applicaDesignSystem(ds)
}
