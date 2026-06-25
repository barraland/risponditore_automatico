-- ============================================================
-- Setup Supabase — schema completo + permessi per la SPA (CRM HORECA).
-- Esegui nel SQL Editor di Supabase. Idempotente: crea ciò che manca, non distrugge nulla.
-- Serve sia per un ACCOUNT NUOVO (crea tutte le tabelle da zero) sia per migrare/aggiornare
-- un account esistente (aggiunge tabelle/colonne mancanti).
--
-- Accesso: gli utenti AUTENTICATI possono leggere e scrivere tutte le righe (RLS permissiva).
-- Il multi-tenant vero (tenant_id + policy per tenant) arriverà dopo.
-- ============================================================

-- ---------- 1) SCHEMA: tipi enum + tabelle (generato dai modelli SQLAlchemy) ----------
do $$ begin
  create type contattostato as enum ('CLIENTE', 'PROSPECT');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type direzionemessaggio as enum ('IN', 'OUT');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type prioritaticket as enum ('ALTA', 'MEDIA', 'BASSA');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type statoticket as enum ('APERTO', 'CHIUSO');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type tipoattivita as enum ('RISTORANTE', 'PIZZERIA', 'BAR', 'HOTEL', 'GASTRONOMIA', 'ALTRO');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type statorelazione as enum ('PROSPECT', 'CLIENTE', 'INATTIVO');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type origineordine as enum ('CLIENTE', 'AGENTE');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type canaleordine as enum ('WHATSAPP', 'VOCE', 'EMAIL', 'AGENTE', 'MANUALE');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type statoordine as enum ('BOZZA', 'CONFERMATO', 'EVASO', 'ANNULLATO');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type statodocumento as enum ('PROCESSING', 'READY', 'NEEDS_REVIEW', 'ERROR');
exception when duplicate_object then null;
end $$;

CREATE TABLE IF NOT EXISTS azienda (
	id SERIAL NOT NULL, 
	nome VARCHAR(200) NOT NULL, 
	telefono VARCHAR(30), 
	indirizzo VARCHAR(300), 
	descrizione_servizi TEXT, 
	criteri_priorita TEXT, 
	info_qualificazione TEXT, 
	istruzioni_admin TEXT, 
	prompt_whatsapp TEXT, 
	regole_commerciali TEXT, 
	saluto TEXT, 
	saluto_sconosciuto TEXT, 
	admin_telefoni TEXT, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_azienda_id ON azienda (id);

CREATE TABLE IF NOT EXISTS agenti (
	id SERIAL NOT NULL, 
	nome VARCHAR(100), 
	cognome VARCHAR(100), 
	telefono VARCHAR(30), 
	email VARCHAR(150), 
	zona VARCHAR(150), 
	percentuale_provvigione FLOAT, 
	note TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_agenti_id ON agenti (id);

CREATE TABLE IF NOT EXISTS locali (
	id SERIAL NOT NULL, 
	insegna VARCHAR(200) NOT NULL, 
	ragione_sociale VARCHAR(200), 
	tipo tipoattivita NOT NULL, 
	piva VARCHAR(20), 
	indirizzo VARCHAR(300), 
	citta VARCHAR(120), 
	stato_relazione statorelazione NOT NULL, 
	agente_referente_id INTEGER, 
	note TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(agente_referente_id) REFERENCES agenti (id)
);

CREATE INDEX IF NOT EXISTS ix_locali_id ON locali (id);

CREATE INDEX IF NOT EXISTS ix_locali_agente_referente_id ON locali (agente_referente_id);

CREATE INDEX IF NOT EXISTS ix_locali_stato_relazione ON locali (stato_relazione);

CREATE INDEX IF NOT EXISTS ix_locali_citta ON locali (citta);

CREATE TABLE IF NOT EXISTS documenti (
	id SERIAL NOT NULL, 
	azienda_id INTEGER, 
	categoria VARCHAR(40) NOT NULL, 
	anno INTEGER, 
	nome_file VARCHAR(300) NOT NULL, 
	percorso VARCHAR(500) NOT NULL, 
	storage_path VARCHAR(500), 
	n_pagine INTEGER, 
	dimensione INTEGER, 
	stato statodocumento NOT NULL, 
	errore TEXT, 
	indice_raw TEXT, 
	caricato_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(azienda_id) REFERENCES azienda (id)
);

CREATE INDEX IF NOT EXISTS ix_documenti_id ON documenti (id);

CREATE INDEX IF NOT EXISTS ix_documenti_anno ON documenti (anno);

CREATE TABLE IF NOT EXISTS testi_categoria (
	id SERIAL NOT NULL, 
	azienda_id INTEGER, 
	categoria VARCHAR(40) NOT NULL, 
	testo TEXT, 
	aggiornato_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(azienda_id) REFERENCES azienda (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_testi_categoria_categoria ON testi_categoria (categoria);

CREATE INDEX IF NOT EXISTS ix_testi_categoria_id ON testi_categoria (id);

CREATE TABLE IF NOT EXISTS contatti (
	id SERIAL NOT NULL, 
	titolo VARCHAR(20), 
	nome VARCHAR(100), 
	cognome VARCHAR(100), 
	ragione_sociale VARCHAR(200), 
	ruolo VARCHAR(150), 
	email VARCHAR(150), 
	telefono VARCHAR(30), 
	sede VARCHAR(200), 
	stato contattostato NOT NULL, 
	note TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	locale_id INTEGER, 
	is_primario BOOLEAN, 
	PRIMARY KEY (id), 
	FOREIGN KEY(locale_id) REFERENCES locali (id)
);

CREATE INDEX IF NOT EXISTS ix_contatti_locale_id ON contatti (locale_id);

CREATE INDEX IF NOT EXISTS ix_contatti_id ON contatti (id);

CREATE TABLE IF NOT EXISTS sezioni (
	id SERIAL NOT NULL, 
	documento_id INTEGER NOT NULL, 
	ordine INTEGER NOT NULL, 
	titolo VARCHAR(400) NOT NULL, 
	summary TEXT, 
	page_start INTEGER NOT NULL, 
	page_end INTEGER NOT NULL, 
	contiene_tabelle BOOLEAN, 
	content_md TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(documento_id) REFERENCES documenti (id)
);

CREATE INDEX IF NOT EXISTS ix_sezioni_id ON sezioni (id);

CREATE TABLE IF NOT EXISTS messaggi_chat (
	id SERIAL NOT NULL, 
	contatto_id INTEGER NOT NULL, 
	direzione direzionemessaggio NOT NULL, 
	testo TEXT NOT NULL, 
	traccia TEXT, 
	timestamp TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(contatto_id) REFERENCES contatti (id)
);

CREATE INDEX IF NOT EXISTS ix_messaggi_chat_id ON messaggi_chat (id);

CREATE TABLE IF NOT EXISTS chiamate_voce (
	id SERIAL NOT NULL, 
	contatto_id INTEGER NOT NULL, 
	telefono VARCHAR(30), 
	iniziata_at TIMESTAMP WITHOUT TIME ZONE, 
	durata_sec INTEGER, 
	trascrizione TEXT, 
	riassunto TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(contatto_id) REFERENCES contatti (id)
);

CREATE INDEX IF NOT EXISTS ix_chiamate_voce_id ON chiamate_voce (id);

CREATE TABLE IF NOT EXISTS ticket (
	id SERIAL NOT NULL, 
	contatto_id INTEGER, 
	canale VARCHAR(20), 
	titolo VARCHAR(300) NOT NULL, 
	priorita prioritaticket, 
	descrizione TEXT, 
	storia TEXT, 
	stato statoticket, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(contatto_id) REFERENCES contatti (id)
);

CREATE INDEX IF NOT EXISTS ix_ticket_stato ON ticket (stato);

CREATE INDEX IF NOT EXISTS ix_ticket_created_at ON ticket (created_at);

CREATE INDEX IF NOT EXISTS ix_ticket_id ON ticket (id);

CREATE INDEX IF NOT EXISTS ix_ticket_priorita ON ticket (priorita);

CREATE TABLE IF NOT EXISTS ordini (
	id SERIAL NOT NULL, 
	locale_id INTEGER NOT NULL, 
	contatto_id INTEGER, 
	agente_id INTEGER, 
	origine origineordine NOT NULL, 
	canale canaleordine NOT NULL, 
	stato statoordine NOT NULL, 
	data TIMESTAMP WITHOUT TIME ZONE, 
	note TEXT, 
	descrizione_agente TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(locale_id) REFERENCES locali (id), 
	FOREIGN KEY(contatto_id) REFERENCES contatti (id), 
	FOREIGN KEY(agente_id) REFERENCES agenti (id)
);

CREATE INDEX IF NOT EXISTS ix_ordini_stato ON ordini (stato);

CREATE INDEX IF NOT EXISTS ix_ordini_data ON ordini (data);

CREATE INDEX IF NOT EXISTS ix_ordini_locale_id ON ordini (locale_id);

CREATE INDEX IF NOT EXISTS ix_ordini_id ON ordini (id);

CREATE TABLE IF NOT EXISTS promemoria (
	id SERIAL NOT NULL, 
	contatto_id INTEGER NOT NULL, 
	testo TEXT NOT NULL, 
	scade_il TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(contatto_id) REFERENCES contatti (id)
);

CREATE INDEX IF NOT EXISTS ix_promemoria_id ON promemoria (id);

CREATE INDEX IF NOT EXISTS ix_promemoria_contatto_id ON promemoria (contatto_id);

CREATE INDEX IF NOT EXISTS ix_promemoria_created_at ON promemoria (created_at);

CREATE TABLE IF NOT EXISTS risposte_ticket (
	id SERIAL NOT NULL, 
	ticket_id INTEGER NOT NULL, 
	testo TEXT NOT NULL, 
	inviata_email BOOLEAN, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(ticket_id) REFERENCES ticket (id)
);

CREATE INDEX IF NOT EXISTS ix_risposte_ticket_id ON risposte_ticket (id);

CREATE TABLE IF NOT EXISTS righe_ordine (
	id SERIAL NOT NULL, 
	ordine_id INTEGER NOT NULL, 
	descrizione VARCHAR(400) NOT NULL, 
	quantita FLOAT, 
	unita VARCHAR(30), 
	prezzo_unitario FLOAT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(ordine_id) REFERENCES ordini (id)
);

CREATE INDEX IF NOT EXISTS ix_righe_ordine_id ON righe_ordine (id);

-- ---------- 2) MIGRAZIONI: colonne aggiunte nel tempo (no-op se gia' presenti) ----------
-- (Per i DB esistenti le cui tabelle precedono queste colonne; su un DB nuovo sono gia' incluse sopra.)
alter table public.azienda    add column if not exists istruzioni_admin   text;
alter table public.azienda    add column if not exists regole_commerciali text;
alter table public.azienda    add column if not exists prompt_whatsapp    text;
alter table public.azienda    add column if not exists admin_telefoni     text;
alter table public.azienda    add column if not exists saluto             text;
alter table public.azienda    add column if not exists saluto_sconosciuto text;
alter table public.documenti  add column if not exists storage_path       varchar(500);
alter table public.contatti   add column if not exists titolo             varchar(20);


-- ---------- 3) PERMESSI: RLS + grant per il ruolo 'authenticated' (la SPA) ----------
do $$
declare t text;
begin
  foreach t in array array[
    'locali','agenti','contatti','ordini','righe_ordine','azienda','documenti','sezioni',
    'testi_categoria','ticket','messaggi_chat','chiamate_voce','risposte_ticket','promemoria'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
    execute format('drop policy if exists auth_all on public.%I', t);
    execute format('create policy auth_all on public.%I for all to authenticated using (true) with check (true)', t);
  end loop;
  execute 'grant usage, select on all sequences in schema public to authenticated';
end $$;

-- ---------- 4) STORAGE: bucket privato documenti + accesso autenticati ----------
insert into storage.buckets (id, name, public) values ('documenti', 'documenti', false)
  on conflict (id) do nothing;
drop policy if exists doc_auth_all on storage.objects;
create policy doc_auth_all on storage.objects for all to authenticated
  using (bucket_id = 'documenti') with check (bucket_id = 'documenti');

-- ---------- 5) Utente di login ----------
-- Supabase -> Authentication -> Users -> Add user (email + password, "Auto Confirm User").
-- I dati demo (contatti/ordini) li popola il backend al primo avvio se il DB e' vuoto.
