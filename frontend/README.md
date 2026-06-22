# HORECA — frontend (Pipework)

SPA React (Vite + TypeScript) che parla **direttamente a Supabase** (dati + auth) con il
tema **Pipework**. Fetta verticale attuale: **login + lista/scheda Società** (sola lettura).
Pensata per il deploy su **Vercel**.

## Avvio in locale
```bash
cd frontend
cp .env.example .env.local        # compila VITE_SUPABASE_URL e VITE_SUPABASE_ANON_KEY
npm install
npm run dev                       # http://localhost:5173
```
- `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY`: in Supabase → Project Settings → API.
  L'anon key è **pubblica** (la sicurezza la fa RLS).

## Setup Supabase (una volta)
1. Esegui `supabase_setup.sql` nel **SQL Editor** (abilita RLS + lettura per utenti autenticati).
2. Crea un utente: **Authentication → Users → Add user** (email + password) per fare login.

## Deploy su Vercel
1. Push del repo su GitHub (la cartella `frontend/` può stare nello stesso repo del backend).
2. Vercel → **Add New Project** → importa il repo.
   - **Root Directory**: `frontend`
   - Framework preset: **Vite** (build `npm run build`, output `dist`)
3. **Environment Variables** (Project Settings → Environment Variables):
   - `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (e opzionale `VITE_API_BASE`).
4. Deploy. Da lì ogni push aggiorna il sito.

`vercel.json` gestisce il rewrite SPA (tutte le route → `index.html`).

## Architettura
- **SPA** → Supabase (CRUD + auth via `supabase-js`, RLS per la sicurezza/multi-tenant).
- **SPA** → FastAPI (`VITE_API_BASE`) solo per le azioni AI (retriever) — da agganciare più avanti.
- Il backend FastAPI resta su Azure per voce/WhatsApp/MCP e i webhook.

## Prossimi step
- `tenant_id` + policy RLS per tenant (multi-tenant vero).
- Scrittura (crea/modifica) con policy insert/update.
- Altre sezioni: Ordini, Agenti, Contatti.
