import { useEffect, useState } from 'react'
import { supabase } from './supabase'

// Label rinominabili per vertical (catalogo/ordine/riga). Lette da azienda.commercio_labels (JSON).
// Default HORECA: Prodotti / Ordini / Righe. Altri verticali possono rinominarle (es. Servizi/Prenotazioni).
export type LabelPair = { sing: string; plur: string }
export type CommercioLabels = { catalogo: LabelPair; ordine: LabelPair; riga: LabelPair }

export const DEFAULT_LABELS: CommercioLabels = {
  catalogo: { sing: 'Prodotto', plur: 'Prodotti' },
  ordine: { sing: 'Ordine', plur: 'Ordini' },
  riga: { sing: 'Riga', plur: 'Righe' },
}

export function parseLabels(raw: string | null | undefined): CommercioLabels {
  if (!raw) return DEFAULT_LABELS
  try {
    const v = JSON.parse(raw) || {}
    return {
      catalogo: { ...DEFAULT_LABELS.catalogo, ...(v.catalogo || {}) },
      ordine: { ...DEFAULT_LABELS.ordine, ...(v.ordine || {}) },
      riga: { ...DEFAULT_LABELS.riga, ...(v.riga || {}) },
    }
  } catch {
    return DEFAULT_LABELS
  }
}

// Hook: legge le label del tenant attivo. Resiliente: se la colonna non esiste (migrazione non
// ancora lanciata) o errore → default. Ritorna anche un reload per aggiornare dopo il salvataggio.
export function useCommercioLabels(aziendaId: number | null): [CommercioLabels, () => void] {
  const [labels, setLabels] = useState<CommercioLabels>(DEFAULT_LABELS)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!aziendaId) { setLabels(DEFAULT_LABELS); return }
    supabase.from('azienda').select('commercio_labels').eq('id', aziendaId).maybeSingle()
      .then(({ data, error }) => {
        if (error) { setLabels(DEFAULT_LABELS); return }
        setLabels(parseLabels((data as any)?.commercio_labels))
      })
  }, [aziendaId, tick])
  return [labels, () => setTick(t => t + 1)]
}
