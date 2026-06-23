-- ============================================================
-- Setup Supabase per la SPA (CRM HORECA).
-- Esegui nel SQL Editor di Supabase UNA volta (ri-eseguibile: è idempotente).
--
-- Per ora: utenti AUTENTICATI possono leggere E scrivere tutte le righe.
-- Il multi-tenant vero (tenant_id + policy che filtra per tenant) arriva dopo.
-- ============================================================

-- Colonna per le istruzioni libere dell'assistente (config editabile dalla SPA).
alter table public.azienda add column if not exists istruzioni_admin text;
-- Regole commerciali e promozioni (prezzi, sconti, omaggi), iniettate ovunque.
alter table public.azienda add column if not exists regole_commerciali text;
-- Prompt dell'agente WhatsApp (separato da quello vocale).
alter table public.azienda add column if not exists prompt_whatsapp text;
-- Formule del primo saluto vocale (segnaposto {nome} {cognome} {azienda}).
alter table public.azienda add column if not exists saluto text;                -- cliente riconosciuto
alter table public.azienda add column if not exists saluto_sconosciuto text;    -- chiamante anonimo
-- Path su Supabase Storage dell'originale durevole del documento.
alter table public.documenti add column if not exists storage_path varchar(500);
-- Appellativo del contatto ("Signore"/"Signora"), per il saluto corretto.
alter table public.contatti add column if not exists titolo varchar(20);

do $$
declare t text;
begin
  foreach t in array array['locali','agenti','contatti','ordini','righe_ordine','azienda','documenti','sezioni','testi_categoria','ticket','messaggi_chat','chiamate_voce','risposte_ticket'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
    -- una policy unica "tutto agli autenticati"
    execute format('drop policy if exists auth_all on public.%I', t);
    execute format('create policy auth_all on public.%I for all to authenticated using (true) with check (true)', t);
  end loop;
  -- le sequence degli id servono per gli INSERT
  execute 'grant usage, select on all sequences in schema public to authenticated';
end $$;

-- Storage: bucket privato per i documenti + accesso agli utenti autenticati.
insert into storage.buckets (id, name, public) values ('documenti', 'documenti', false)
  on conflict (id) do nothing;
drop policy if exists doc_auth_all on storage.objects;
create policy doc_auth_all on storage.objects for all to authenticated
  using (bucket_id = 'documenti') with check (bucket_id = 'documenti');

-- Crea un utente per il login dalla dashboard:
-- Supabase → Authentication → Users → Add user (email + password, "Auto Confirm User").
