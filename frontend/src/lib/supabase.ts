import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL as string
// Accetta sia il nuovo nome (publishable) sia il vecchio (anon).
const anon = (import.meta.env.VITE_SUPABASE_ANON_KEY
  || import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY) as string

if (!url || !anon) {
  console.error('Mancano VITE_SUPABASE_URL e VITE_SUPABASE_ANON_KEY/_PUBLISHABLE_KEY (vedi .env.example).')
}

export const supabase = createClient(url, anon)
