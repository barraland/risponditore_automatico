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


-- ============================================================
-- STEP 4 — RLS PER-TENANT + SUPER-ADMIN (cross-tenant switcher).
-- Ruoli:
--   * super_admin(user_id)      -> Pipework: vede/gestisce TUTTI i tenant.
--   * utente_azienda(user,az)   -> utente-cliente: vede SOLO il suo tenant.
-- La RLS è l'unica barriera d'isolamento perché la SPA parla direttamente con Supabase.
-- Idempotente.
-- ============================================================

create table if not exists public.super_admin (
  user_id uuid primary key references auth.users(id) on delete cascade,
  creato_il timestamptz default now()
);

create table if not exists public.utente_azienda (
  user_id    uuid    references auth.users(id) on delete cascade,
  azienda_id integer references public.azienda(id) on delete cascade,
  creato_il  timestamptz default now(),
  primary key (user_id, azienda_id)
);
create index if not exists ix_utente_azienda_user on public.utente_azienda(user_id);

-- Helper (SECURITY DEFINER: leggono super_admin/utente_azienda senza ricorsione RLS).
create or replace function public.is_super_admin() returns boolean
  language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.super_admin where user_id = auth.uid());
$$;

create or replace function public.tenant_ids() returns setof integer
  language sql stable security definer set search_path = public as $$
  select azienda_id from public.utente_azienda where user_id = auth.uid();
$$;

-- Un utente può "vedere" un tenant se è super-admin oppure ne è membro.
create or replace function public.can_see_tenant(aid integer) returns boolean
  language sql stable security definer set search_path = public as $$
  select public.is_super_admin() or aid in (select public.tenant_ids());
$$;

-- Pulisce le policy esistenti (incl. la vecchia "using(true)") sulle tabelle-tenant.
do $$
declare
  tbl text;
  pol record;
  tabelle text[] := array[
    'azienda','contatti','ticket','agenti','locali','ordini','promemoria',
    'amministratori','inoltri','documenti','testi_categoria','google_calendar',
    'righe_ordine','sezioni','super_admin','utente_azienda'
  ];
begin
  foreach tbl in array tabelle loop
    if to_regclass('public.'||tbl) is null then continue; end if;
    execute format('alter table public.%I enable row level security', tbl);
    for pol in select policyname from pg_policies where schemaname='public' and tablename=tbl loop
      execute format('drop policy if exists %I on public.%I', pol.policyname, tbl);
    end loop;
  end loop;
end $$;

-- Meta-tabelle dei ruoli: ognuno legge le proprie righe; il super-admin gestisce tutto.
create policy sa_read on public.super_admin for select to authenticated
  using (user_id = auth.uid() or public.is_super_admin());
create policy sa_write on public.super_admin for all to authenticated
  using (public.is_super_admin()) with check (public.is_super_admin());

create policy ua_read on public.utente_azienda for select to authenticated
  using (user_id = auth.uid() or public.is_super_admin());
create policy ua_write on public.utente_azienda for all to authenticated
  using (public.is_super_admin()) with check (public.is_super_admin());

-- azienda: la vedi se ne fai parte (o super-admin); crea/modifica solo super-admin.
create policy tenant_read on public.azienda for select to authenticated
  using (public.can_see_tenant(id));
create policy tenant_write on public.azienda for all to authenticated
  using (public.is_super_admin()) with check (public.is_super_admin());

-- Tabelle-tenant "dirette" (colonna azienda_id): stessa policy uniforme.
do $$
declare
  tbl text;
  tabelle text[] := array[
    'contatti','ticket','agenti','locali','ordini','promemoria',
    'amministratori','inoltri','documenti','testi_categoria','google_calendar'
  ];
begin
  foreach tbl in array tabelle loop
    if to_regclass('public.'||tbl) is null then continue; end if;
    execute format($f$
      create policy tenant_all on public.%I for all to authenticated
      using (public.can_see_tenant(azienda_id))
      with check (public.can_see_tenant(azienda_id))
    $f$, tbl);
  end loop;
end $$;

-- Tabelle-figlie (niente azienda_id): tenant risolto via il genitore.
do $$ begin
  if to_regclass('public.righe_ordine') is not null then
    create policy tenant_all on public.righe_ordine for all to authenticated
      using (exists (select 1 from public.ordini o
                     where o.id = righe_ordine.ordine_id and public.can_see_tenant(o.azienda_id)))
      with check (exists (select 1 from public.ordini o
                          where o.id = righe_ordine.ordine_id and public.can_see_tenant(o.azienda_id)));
  end if;
  if to_regclass('public.sezioni') is not null then
    create policy tenant_all on public.sezioni for all to authenticated
      using (exists (select 1 from public.documenti d
                     where d.id = sezioni.documento_id and public.can_see_tenant(d.azienda_id)))
      with check (exists (select 1 from public.documenti d
                          where d.id = sezioni.documento_id and public.can_see_tenant(d.azienda_id)));
  end if;
end $$;

-- testi_categoria: la nota è UNA per (tenant, categoria), non globale per categoria.
drop index if exists public.ix_testi_categoria_categoria;
alter table public.testi_categoria drop constraint if exists testi_categoria_categoria_key;
create unique index if not exists ux_testi_categoria_az_cat
  on public.testi_categoria(azienda_id, categoria);
