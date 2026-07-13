// Zoom dell'area contenuti (densità): scala testo e spaziatura della pagina, senza toccare la
// sidebar (che resta a tutta altezza). Utile per stare più comodi su portatile vs 4K.
// Persistito per-browser; applicato via la CSS var --ui-scale su .pw-container (vedi index.css).

const KEY = 'pw-ui-scale'
export const UI_SCALE = { MIN: 0.8, MAX: 1.15, STEP: 0.05, DEFAULT: 1 }

export function getUiScale(): number {
  try {
    const v = parseFloat(localStorage.getItem(KEY) || '')
    if (!isNaN(v)) return Math.min(UI_SCALE.MAX, Math.max(UI_SCALE.MIN, v))
  } catch { /* localStorage non disponibile */ }
  return UI_SCALE.DEFAULT
}

export function applyUiScale(s: number = getUiScale()): void {
  document.documentElement.style.setProperty('--ui-scale', String(s))
}

export function setUiScale(s: number): number {
  const clamped = Math.min(UI_SCALE.MAX, Math.max(UI_SCALE.MIN, Math.round(s / UI_SCALE.STEP) * UI_SCALE.STEP))
  try { localStorage.setItem(KEY, String(clamped)) } catch { /* ignore */ }
  applyUiScale(clamped)
  return clamped
}
