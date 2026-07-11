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

-- Pulizia: azienda.telefono era un campo morto (mai letto da prompt/init/tool). Rimosso.
alter table public.azienda drop column if exists telefono;

-- ============================================================
-- Prompt vocale MODULARE: override per-tenant dei moduli (i default vivono nel backend).
-- create_all crea comunque la tabella; qui la creiamo esplicitamente + RLS (niente accesso
-- via PostgREST fuori dal proprio tenant). L'assemblaggio backend usa SQLAlchemy (bypassa RLS).
-- ============================================================
create table if not exists public.prompt_modulo (
  id            serial primary key,
  azienda_id    integer references public.azienda(id) on delete cascade,
  chiave        varchar(60) not null,
  titolo        varchar(120),
  ordine        integer,
  attivo        boolean,
  testo         text,
  aggiornato_at timestamptz default now()
);
create unique index if not exists ux_prompt_modulo_az_chiave on public.prompt_modulo(azienda_id, chiave);
create index if not exists ix_prompt_modulo_azienda on public.prompt_modulo(azienda_id);

alter table public.prompt_modulo enable row level security;
drop policy if exists tenant_all on public.prompt_modulo;
create policy tenant_all on public.prompt_modulo for all to authenticated
  using (public.can_see_tenant(azienda_id))
  with check (public.can_see_tenant(azienda_id));

-- Prompt modulare multicanale: applicabilità per canale + varianti di testo per canale.
alter table public.prompt_modulo add column if not exists canali       text;  -- JSON list dei canali
alter table public.prompt_modulo add column if not exists testi_canale text;  -- JSON {canale: testo}

-- Contatti: campo note (testo libero) per contesto specifico del business (es. veterinario).
alter table public.contatti add column if not exists note text;

-- Personalizzazione GUI per-tenant: nascondi voci di menù non pertinenti (None/True = mostra, False = nascondi).
alter table public.azienda add column if not exists mostra_ordini     boolean;
alter table public.azienda add column if not exists mostra_agenti     boolean;
alter table public.azienda add column if not exists mostra_calendario boolean;

-- Documenti: nota interpretativa PER FILE (scritta dall'admin, letta dal retriever).
alter table public.documenti add column if not exists note text;

-- Google: scope concessi (per sapere se la connessione include gmail.send = invio email per-tenant).
alter table public.google_calendar add column if not exists scopes text;

-- Inoltri: calendario dedicato + regole di prenotazione per ogni persona della rubrica.
alter table public.inoltri add column if not exists calendar_id          varchar(200);
alter table public.inoltri add column if not exists regole_prenotazione  text;

-- Inoltri: flag admin (dal suo numero, voce/WhatsApp, parte il prompt amministratore).
alter table public.inoltri add column if not exists admin boolean default false;

-- Prompt moduli: dimensione PUBBLICO (cliente/admin) per i moduli custom del tenant.
alter table public.prompt_modulo add column if not exists audience varchar(20);

-- ============================================================
-- Log conversazioni MULTICANALE: chiamate_voce diventa il registro delle interazioni
-- (voce/whatsapp/mail) con link al ticket. Serve azienda_id per RLS/tenant + canale + ticket_id.
-- ============================================================
alter table public.chiamate_voce add column if not exists azienda_id integer references public.azienda(id);
alter table public.chiamate_voce add column if not exists canale     varchar(20) default 'voce';
alter table public.chiamate_voce add column if not exists ticket_id  integer references public.ticket(id) on delete set null;
create index if not exists ix_chiamate_azienda on public.chiamate_voce(azienda_id);
create index if not exists ix_chiamate_ticket  on public.chiamate_voce(ticket_id);
-- backfill: tenant dal contatto, canale storico = voce
update public.chiamate_voce cv set azienda_id = c.azienda_id
  from public.contatti c where cv.contatto_id = c.id and cv.azienda_id is null;
update public.chiamate_voce set canale = 'voce' where canale is null;
-- RLS: stessa policy tenant delle altre tabelle (la SPA legge via PostgREST)
alter table public.chiamate_voce enable row level security;
drop policy if exists tenant_all on public.chiamate_voce;
create policy tenant_all on public.chiamate_voce for all to authenticated
  using (public.can_see_tenant(azienda_id))
  with check (public.can_see_tenant(azienda_id));

-- Saluto d'apertura dedicato all'AMMINISTRATORE (init voce admin), editabile in dashboard.
alter table public.azienda add column if not exists saluto_admin text;

-- Documenti "Sempre presente": fuori dal retriever, iniettati per intero e sempre nel prompt.
alter table public.documenti add column if not exists sempre_contesto boolean default false;

-- ============================================================
-- Entità generiche customizzabili (vet=animale, onoranze=deceduto...). N:N, chiavi surrogate.
-- entita_tipo = config per tenant; entita = istanze; contatto_entita = legame N:N.
-- ============================================================
create table if not exists public.entita_tipo (
  id                serial primary key,
  azienda_id        integer references public.azienda(id) on delete cascade,
  nome_singolare    varchar(80) not null,
  nome_plurale      varchar(80),
  max_per_contatto  integer default 0,          -- 0 = illimitato; 1 = uno solo
  condivisibile     boolean default true,       -- un'entità può stare su più contatti?
  campo_etichetta   varchar(60),                -- chiave del campo che fa da "nome"
  campi             text,                        -- JSON [{chiave,label,tipo,obbligatorio,opzioni}]
  attivo            boolean default true,
  created_at        timestamptz default now()
);
create index if not exists ix_entita_tipo_azienda on public.entita_tipo(azienda_id);

create table if not exists public.entita (
  id          serial primary key,
  azienda_id  integer references public.azienda(id) on delete cascade,
  tipo_id     integer references public.entita_tipo(id) on delete cascade,
  etichetta   varchar(200),
  valori      text,                              -- JSON {chiave: valore}
  created_at  timestamptz default now()
);
create index if not exists ix_entita_azienda on public.entita(azienda_id);
create index if not exists ix_entita_tipo    on public.entita(tipo_id);

create table if not exists public.contatto_entita (
  id          serial primary key,
  azienda_id  integer references public.azienda(id) on delete cascade,
  contatto_id integer references public.contatti(id) on delete cascade,
  entita_id   integer references public.entita(id) on delete cascade,
  ruolo       varchar(60),
  created_at  timestamptz default now()
);
create index if not exists ix_contatto_entita_azienda  on public.contatto_entita(azienda_id);
create index if not exists ix_contatto_entita_contatto on public.contatto_entita(contatto_id);
create index if not exists ix_contatto_entita_entita   on public.contatto_entita(entita_id);

do $$ declare t text; begin
  foreach t in array array['entita_tipo','entita','contatto_entita'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists tenant_all on public.%I', t);
    execute format('create policy tenant_all on public.%I for all to authenticated '
                   'using (public.can_see_tenant(azienda_id)) with check (public.can_see_tenant(azienda_id))', t);
  end loop;
end $$;

-- Override (globale) delle descrizioni dei tool, editabile da dashboard (letto solo dal backend).
create table if not exists public.tool_descrizione (
  id          serial primary key,
  tool_name   varchar(80) unique not null,
  descrizione text,
  updated_at  timestamptz default now()
);
alter table public.tool_descrizione enable row level security;  -- accesso solo via backend

-- Campi della PERSONA (contatto) che l'assistente chiede SEMPRE: JSON list (nome/cognome/telefono/email/ruolo).
alter table public.azienda add column if not exists contatto_obbligatori text;
