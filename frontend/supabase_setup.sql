-- ============================================================
-- Setup Supabase per la SPA (CRM HORECA) — uso quotidiano.
-- Esegui nel SQL Editor. Idempotente: aggiunge ciò che manca, non droppa né sovrascrive nulla.
--
-- Le tabelle base le crea il backend al primo avvio (create_all). Qui aggiungiamo solo:
--  - le tabelle/colonne NUOVE non ancora presenti,
--  - i permessi (RLS) per la SPA e lo Storage.
-- Per creare TUTTO da zero (es. migrare su un account nuovo) usa invece: schema_completo.sql
-- ============================================================

-- ---------- Tabelle nuove: promemoria (note per cliente) + amministratori ----------
create table if not exists public.promemoria (
  id          serial primary key,
  contatto_id integer not null references public.contatti(id),
  testo       text not null,
  scade_il    timestamp,
  created_at  timestamp default now()
);

create table if not exists public.amministratori (
  id         serial primary key,
  nome       varchar(150),
  telefono   varchar(30) not null,
  created_at timestamp default now()
);

create table if not exists public.inoltri (
  id         serial primary key,
  nome       varchar(100),
  cognome    varchar(100),
  ruolo      varchar(150),
  email      varchar(150),
  telefono   varchar(30) not null,
  regole     text,
  created_at timestamp default now()
);

-- Ricerca semantica documenti: chunk + embedding (JSON) + metadati per filtro/citazione.
create table if not exists public.documento_chunk (
  id           serial primary key,
  documento_id integer not null references public.documenti(id) on delete cascade,
  sezione_id   integer references public.sezioni(id) on delete cascade,
  ordine       integer not null default 0,
  categoria    varchar(40),
  page_start   integer,
  page_end     integer,
  testo        text not null,
  embedding    text
);
create index if not exists ix_documento_chunk_documento on public.documento_chunk(documento_id);
create index if not exists ix_documento_chunk_categoria on public.documento_chunk(categoria);
-- pgvector: la similarità coseno la calcola il DB. 1536 = dim. di text-embedding-3-small.
create extension if not exists vector;
alter table public.documento_chunk add column if not exists embedding_vec vector(1536);
-- (opzionale, per scalare) indice ANN:
-- create index if not exists ix_documento_chunk_vec on public.documento_chunk
--   using hnsw (embedding_vec vector_cosine_ops);

-- Interrogazione file tabellari (CSV/Excel): colonne con facet + righe (JSON).
create table if not exists public.documento_colonna (
  id           serial primary key,
  documento_id integer not null references public.documenti(id) on delete cascade,
  nome         varchar(200) not null,
  tipo         varchar(20),
  n_distinti   integer default 0,
  esaustivo    boolean default true,
  distinti     text,
  min_val      varchar(120),
  max_val      varchar(120)
);
create index if not exists ix_documento_colonna_documento on public.documento_colonna(documento_id);

create table if not exists public.documento_riga (
  id           serial primary key,
  documento_id integer not null references public.documenti(id) on delete cascade,
  ordine       integer not null default 0,
  dati         text not null
);
create index if not exists ix_documento_riga_documento on public.documento_riga(documento_id);

-- ---------- Colonne aggiunte nel tempo (no-op se già presenti) ----------
alter table public.azienda   add column if not exists istruzioni_admin   text;
alter table public.azienda   add column if not exists regole_commerciali text;
alter table public.azienda   add column if not exists prompt_whatsapp    text;
alter table public.azienda   add column if not exists admin_telefoni     text;
alter table public.azienda   add column if not exists saluto             text;
alter table public.azienda   add column if not exists saluto_sconosciuto text;
alter table public.documenti add column if not exists storage_path       varchar(500);
alter table public.documenti add column if not exists riassunto          text;
alter table public.documenti     add column if not exists inviabile      boolean not null default true;
alter table public.contatti      add column if not exists titolo         varchar(20);
alter table public.amministratori add column if not exists email          varchar(150);
alter table public.amministratori add column if not exists inoltra_alta   boolean not null default false;
alter table public.amministratori add column if not exists inoltra_media  boolean not null default false;
alter table public.amministratori add column if not exists inoltra_bassa  boolean not null default false;

-- ---------- Permessi: RLS + grant per il ruolo 'authenticated' (la SPA) ----------
do $$
declare t text;
begin
  foreach t in array array[
    'locali','agenti','contatti','ordini','righe_ordine','azienda','documenti','sezioni',
    'testi_categoria','ticket','messaggi_chat','chiamate_voce','risposte_ticket','promemoria','amministratori','inoltri','documento_chunk','documento_colonna','documento_riga'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
    execute format('drop policy if exists auth_all on public.%I', t);
    execute format('create policy auth_all on public.%I for all to authenticated using (true) with check (true)', t);
  end loop;
  execute 'grant usage, select on all sequences in schema public to authenticated';
end $$;

-- ---------- Storage: bucket privato documenti + accesso autenticati ----------
insert into storage.buckets (id, name, public) values ('documenti', 'documenti', false)
  on conflict (id) do nothing;
drop policy if exists doc_auth_all on storage.objects;
create policy doc_auth_all on storage.objects for all to authenticated
  using (bucket_id = 'documenti') with check (bucket_id = 'documenti');

-- Login: Supabase → Authentication → Users → Add user (email + password, "Auto Confirm User").
