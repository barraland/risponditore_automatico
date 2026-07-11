"""Toolset condiviso per gli agenti in function-calling (oggi: WhatsApp).

Espone gli STESSI strumenti che ElevenLabs usa via MCP, riusando le implementazioni in
`routers/mcp_server.py` (unica fonte di verità: voce e WhatsApp non divergono più).

- `SCHEMI`: definizioni OpenAI tools (function-calling). `telefono` e `tenant` NON sono nei
  parametri: li inietta il codice (`esegui`), così il modello non deve/può sbagliarli.
- `esegui(nome, args, telefono, tenant)`: dispatcher → chiama la funzione MCP corrispondente.

Esclusi di proposito (non hanno senso su chat / sono flussi diversi): chiama_persona, attendi_esito,
inoltra_chiamata, unisci_chiamate, rifiuta_inoltro (trasferimento di chiamata live) e lascia_promemoria
(flusso amministratore).
"""

import json
import logging

logger = logging.getLogger(__name__)

_ANAGRAFICA_PROPS = {
    "nome": {"type": "string"}, "cognome": {"type": "string"},
    "ragione_sociale": {"type": "string"}, "ruolo": {"type": "string"},
    "email": {"type": "string"}, "sede": {"type": "string"},
    "titolo": {"type": "string", "description": "«Signore»/«Signora», solo se certo del genere."},
    "note": {"type": "string", "description": "Contesto libero sul contatto (si accumula, non sovrascrive)."},
    "stato": {"type": "string", "enum": ["cliente", "prospect"]},
}


def _fn(nome, descrizione, props, required=None):
    return {"type": "function", "function": {
        "name": nome, "description": descrizione,
        "parameters": {"type": "object", "properties": props, "required": required or []},
    }}


SCHEMI = [
    _fn("cerca",
        "Cerca nella base di conoscenza (documenti PDF + tabelle CSV) gli ELEMENTI utili a "
        "rispondere su prodotti, prezzi, disponibilità, condizioni, schede, FAQ. Ritorna estratti "
        "grezzi con la fonte: leggili e formula tu la risposta.",
        {"domanda": {"type": "string"}}, ["domanda"]),
    _fn("salva_contatto",
        "Registra un nuovo contatto in anagrafica. Passa SOLO i campi detti dal cliente; non "
        "inventare. Usalo alla prima registrazione, appena hai almeno il nome.",
        _ANAGRAFICA_PROPS),
    _fn("aggiorna_contatto",
        "Aggiorna i dati anagrafici della PERSONA quando emergono info nuove. `note` si accumula.",
        _ANAGRAFICA_PROPS),
    _fn("aggiorna_locale",
        "Aggiorna i dati del LOCALE/azienda del contatto (città, indirizzo, ragione sociale, P.IVA, "
        "insegna). Passa solo i campi nuovi.",
        {"citta": {"type": "string"}, "indirizzo": {"type": "string"},
         "ragione_sociale": {"type": "string"}, "piva": {"type": "string"}, "insegna": {"type": "string"}}),
    _fn("registra_entita",
        "Registra (o aggiorna) l'ENTITÀ collegata al cliente (es. animale, deceduto, società): il "
        "tipo e i campi da raccogliere sono nel contesto. `valori` = {chiave: valore} dei campi. Passa "
        "`entita_id` SOLO per aggiornare una delle entità GIÀ NOTE elencate nel contesto; ometti per "
        "crearne una nuova. Non dare per scontato che due omonimi siano la stessa: se dubbi, chiedi.",
        {"valori": {"type": "object", "description": "Campi dell'entità {chiave: valore}."},
         "entita_id": {"type": "integer", "description": "Solo per aggiornare un'entità già nota."}},
        ["valori"]),
    _fn("invia_mail",
        "Invia un'email a testo libero al cliente. `testo` obbligatorio (lo scrivi tu). "
        "`categoria_allegato` opzionale per allegare documenti di quella categoria.",
        {"testo": {"type": "string"}, "oggetto": {"type": "string"}, "categoria_allegato": {"type": "string"}},
        ["testo"]),
    _fn("invia_documento",
        "Invia via email un DOCUMENTO specifico (per `documento_id`, preso dalle fonti di `cerca`, "
        "solo se inviabile=true). `email` = quella del cliente (confermata).",
        {"email": {"type": "string"}, "documento_id": {"type": "integer"}, "testo": {"type": "string"}},
        ["email", "documento_id"]),
    _fn("apri_ticket",
        "Apre (o aggiorna) un ticket di follow-up per il team commerciale. Aprilo SEMPRE per il lead, "
        "una sola volta per conversazione.",
        {"titolo": {"type": "string"}, "descrizione": {"type": "string"},
         "priorita": {"type": "string", "enum": ["alta", "media", "bassa"]},
         "trascrizione": {"type": "string"}},
        ["titolo"]),
    _fn("controlla_disponibilita",
        "Slot liberi sul calendario. `giorno` vuoto = prossimi 7 giorni. `persona` = nome/ruolo della "
        "persona della rubrica per cui è il meeting (usa il suo calendario e le sue regole). Proponi "
        "SOLO orari in `slot_liberi` che rispettano anche le `regole_prenotazione` restituite.",
        {"giorno": {"type": "string"}, "durata_minuti": {"type": "integer"},
         "dalle": {"type": "integer"}, "alle": {"type": "integer"}, "persona": {"type": "string"}}),
    _fn("prenota_meeting",
        "Prenota un meeting e invia l'invito (con link Google Meet se online). `data_ora` ISO "
        "(es. 2026-07-01T16:00:00). `persona` = come in controlla_disponibilita (prenota sul suo "
        "calendario). Conferma prima data/ora ed email col cliente.",
        {"titolo": {"type": "string"}, "data_ora": {"type": "string"},
         "durata_minuti": {"type": "integer"}, "invitati": {"type": "string"},
         "descrizione": {"type": "string"}, "online": {"type": "boolean"}, "persona": {"type": "string"}},
        ["titolo", "data_ora"]),
]

# Toolset ADMIN (quando scrive/chiama un amministratore): consulta documenti + lascia promemoria.
SCHEMI_ADMIN = [
    next(s for s in SCHEMI if s["function"]["name"] == "cerca"),
    _fn("lascia_promemoria",
        "Registra un PROMEMORIA per un cliente: lo vedrà l'assistente quando quel cliente scriverà o "
        "chiamerà. `nome_cliente` = nome/cognome del destinatario; `societa` per distinguerlo; "
        "`testo` = l'avviso; `giorni_validita` = validità in giorni (0 = senza scadenza). Se più "
        "clienti corrispondono, ti vengono elencati: chiedi quale e riprova.",
        {"nome_cliente": {"type": "string"}, "testo": {"type": "string"},
         "societa": {"type": "string"}, "giorni_validita": {"type": "integer"}},
        ["nome_cliente", "testo"]),
]

# Nomi consentiti (difesa: ignora tool non previsti). Include client + admin.
NOMI = {s["function"]["name"] for s in SCHEMI} | {s["function"]["name"] for s in SCHEMI_ADMIN}


def esegui(nome: str, args: dict, telefono: str, tenant: str = "") -> dict:
    """Esegue un tool chiamato dal modello, iniettando telefono/tenant. Ritorna il dict del tool."""
    from routers import mcp_server as m   # lazy: evita import circolare (mcp_server importa whatsapp_agent)
    a = dict(args or {})
    # telefono/tenant li iniettiamo NOI: se il modello li ha passati comunque (il prompt glielo dice),
    # scartali per non collidere con l'iniezione (-> "multiple values for keyword argument 'telefono'").
    a.pop("telefono", None)
    a.pop("tenant", None)
    if nome not in NOMI:
        return {"errore": f"strumento sconosciuto: {nome}"}
    try:
        if nome == "cerca":
            return m.cerca(domanda=a.get("domanda", ""), tenant=tenant)
        if nome == "lascia_promemoria":
            return m.lascia_promemoria(telefono=telefono, nome_cliente=a.get("nome_cliente", ""),
                                       testo=a.get("testo", ""), societa=a.get("societa", ""),
                                       giorni_validita=int(a.get("giorni_validita", 0) or 0), tenant=tenant)
        if nome == "salva_contatto":
            return m.salva_contatto(telefono=telefono, tenant=tenant, **a)
        if nome == "aggiorna_contatto":
            return m.aggiorna_contatto(telefono=telefono, tenant=tenant, **a)
        if nome == "aggiorna_locale":
            return m.aggiorna_locale(telefono=telefono, tenant=tenant, **a)
        if nome == "registra_entita":
            return m.registra_entita(telefono=telefono, valori=a.get("valori") or {},
                                     entita_id=int(a.get("entita_id", 0) or 0), tenant=tenant)
        if nome == "registra_ordine":
            righe = [m.RigaOrdineInput(**r) for r in (a.get("righe") or [])]
            return m.registra_ordine(telefono=telefono, righe=righe,
                                     note=a.get("note", ""), conferma=bool(a.get("conferma", False)),
                                     tenant=tenant)
        if nome == "aggiorna_ordine":
            return m.aggiorna_ordine(telefono=telefono, note=a.get("note", ""),
                                     ordine_id=int(a.get("ordine_id", 0) or 0), tenant=tenant)
        if nome == "storico_ordini":
            return m.storico_ordini(telefono=telefono, giorni=int(a.get("giorni", 0) or 0),
                                    limite=int(a.get("limite", 10) or 10), tenant=tenant)
        if nome == "invia_riepilogo_ordine":
            return m.invia_riepilogo_ordine(telefono=telefono, ordine_id=int(a.get("ordine_id", 0) or 0),
                                            tenant=tenant)
        if nome == "invia_mail":
            return m.invia_mail(telefono=telefono, testo=a.get("testo", ""), oggetto=a.get("oggetto", ""),
                                categoria_allegato=a.get("categoria_allegato", ""), tenant=tenant)
        if nome == "invia_documento":
            return m.invia_documento(email=a.get("email", ""), documento_id=int(a.get("documento_id", 0) or 0),
                                     testo=a.get("testo", ""), tenant=tenant)
        if nome == "apri_ticket":
            return m.apri_ticket(telefono=telefono, titolo=a.get("titolo", ""),
                                 descrizione=a.get("descrizione", ""), priorita=a.get("priorita", ""),
                                 trascrizione=a.get("trascrizione", ""), canale="whatsapp", tenant=tenant)
        if nome == "controlla_disponibilita":
            return m.controlla_disponibilita(giorno=a.get("giorno", ""),
                                             durata_minuti=int(a.get("durata_minuti", 30) or 30),
                                             dalle=int(a.get("dalle", 9) or 9),
                                             alle=int(a.get("alle", 18) or 18),
                                             persona=a.get("persona", ""), tenant=tenant)
        if nome == "prenota_meeting":
            return m.prenota_meeting(titolo=a.get("titolo", ""), data_ora=a.get("data_ora", ""),
                                     durata_minuti=int(a.get("durata_minuti", 30) or 30),
                                     invitati=a.get("invitati", ""), descrizione=a.get("descrizione", ""),
                                     online=bool(a.get("online", True)),
                                     persona=a.get("persona", ""), tenant=tenant)
    except Exception as e:
        logger.error("Tool %s fallito: %s", nome, e)
        return {"errore": str(e)}
    return {"errore": f"dispatch mancante per {nome}"}
