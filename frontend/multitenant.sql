-- ============================================================
-- MIGRAZIONE MULTI-TENANT (branch multi-tenant) — Fondamenta.
-- Aggiunge azienda_id (tenant) alle tabelle-cliente + i campi di risoluzione tenant su azienda,
-- poi assegna i dati ESISTENTI al primo tenant. Idempotente.
-- NB: la RLS per-tenant e il resto arrivano negli step successivi.
-- ============================================================

-- Campi per risolvere il tenant dai canali
alter table public.azienda add column if not exists numeri_voce       text;
alter table public.azienda add column if not exists whatsapp_phone_id varchar(60);
create index if not exists ix_azienda_whatsapp_phone_id on public.azienda(whatsapp_phone_id);

-- azienda_id (tenant) sulle tabelle top-level che ne erano prive
alter table public.contatti       add column if not exists azienda_id integer references public.azienda(id);
alter table public.ticket         add column if not exists azienda_id integer references public.azienda(id);
alter table public.agenti         add column if not exists azienda_id integer references public.azienda(id);
alter table public.locali         add column if not exists azienda_id integer references public.azienda(id);
alter table public.ordini         add column if not exists azienda_id integer references public.azienda(id);
alter table public.promemoria     add column if not exists azienda_id integer references public.azienda(id);
alter table public.amministratori add column if not exists azienda_id integer references public.azienda(id);
alter table public.inoltri        add column if not exists azienda_id integer references public.azienda(id);

-- Indici
create index if not exists ix_contatti_azienda       on public.contatti(azienda_id);
create index if not exists ix_ticket_azienda          on public.ticket(azienda_id);
create index if not exists ix_agenti_azienda          on public.agenti(azienda_id);
create index if not exists ix_locali_azienda          on public.locali(azienda_id);
create index if not exists ix_ordini_azienda          on public.ordini(azienda_id);
create index if not exists ix_promemoria_azienda      on public.promemoria(azienda_id);
create index if not exists ix_amministratori_azienda  on public.amministratori(azienda_id);
create index if not exists ix_inoltri_azienda         on public.inoltri(azienda_id);

-- Backfill: tutti i dati esistenti appartengono al PRIMO tenant (l'azienda attuale).
do $$
declare t1 integer;
begin
  select min(id) into t1 from public.azienda;
  if t1 is null then return; end if;
  update public.contatti       set azienda_id = t1 where azienda_id is null;
  update public.ticket         set azienda_id = t1 where azienda_id is null;
  update public.agenti         set azienda_id = t1 where azienda_id is null;
  update public.locali         set azienda_id = t1 where azienda_id is null;
  update public.ordini         set azienda_id = t1 where azienda_id is null;
  update public.promemoria     set azienda_id = t1 where azienda_id is null;
  update public.amministratori set azienda_id = t1 where azienda_id is null;
  update public.inoltri        set azienda_id = t1 where azienda_id is null;
  update public.documenti      set azienda_id = t1 where azienda_id is null;
  update public.testi_categoria set azienda_id = t1 where azienda_id is null;
  update public.google_calendar set azienda_id = t1 where azienda_id is null;
end $$;
