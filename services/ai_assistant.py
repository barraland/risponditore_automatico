"""Entry point + LLM Scheduler per l'elaborazione messaggi WhatsApp.

Flusso:
1. Trova/crea paziente, logga messaggio in arrivo
2. Costruisce system prompt con info studio + stato paziente
3. Chiama GPT con tools (tool_choice=required) → GPT ritorna N tool_calls
4. Se rispondi_paziente → messaggio diretto, skip step 5
5. Altrimenti esegue tool_calls → formatta risposta DETERMINISTICA (no seconda chiamata GPT)
6. Logga risposta, ritorna
"""

import json
import os
import logging
from datetime import datetime

from openai import OpenAI
from sqlalchemy.orm import Session

from database import (
    Paziente, Studio, Servizio, Appuntamento, MessaggioLog,
    StatoAppuntamento, DirezioneMessaggio,
)
from services import tools
from services import calendar_sync

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

SEP_HEAVY = "=" * 72
SEP_LIGHT = "-" * 72

# ---------- Tools schema per OpenAI ----------

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "rispondi_paziente",
            "description": (
                "Invia un messaggio diretto al cliente. "
                "Usa per saluti, risposte generiche, domande di chiarimento, "
                "o qualsiasi risposta che non richiede operazioni."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "messaggio": {
                        "type": "string",
                        "description": "Il testo del messaggio da inviare al cliente",
                    },
                },
                "required": ["messaggio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "registra_paziente",
            "description": "Registra o aggiorna nome e cognome del cliente nel sistema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nome": {"type": "string", "description": "Nome di battesimo"},
                    "cognome": {"type": "string", "description": "Cognome"},
                },
                "required": ["nome"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lista_servizi",
            "description": "Restituisce tutti i servizi offerti dal salone con prezzi e durate.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cerca_disponibilita",
            "description": (
                "Cerca orari disponibili per un servizio. "
                "Se data non specificata, mostra i prossimi giorni con disponibilita."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "servizio": {"type": "string", "description": "Nome del servizio (es. Taglio uomo)"},
                    "data": {"type": "string", "description": "Data YYYY-MM-DD, opzionale"},
                },
                "required": ["servizio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prenota_appuntamento",
            "description": (
                "Prenota un appuntamento per il cliente. "
                "Richiede che il cliente sia registrato con nome e cognome. "
                "Se il cliente non e' registrato, chiama prima registra_paziente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "servizio": {"type": "string", "description": "Nome del servizio"},
                    "data": {"type": "string", "description": "Data YYYY-MM-DD"},
                    "ora": {"type": "string", "description": "Orario HH:MM"},
                },
                "required": ["servizio", "data", "ora"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancella_appuntamento",
            "description": (
                "Cancella un appuntamento attivo del cliente. "
                "Se il cliente ha piu' appuntamenti, specifica servizio e/o data per disambiguare."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "servizio": {
                        "type": "string",
                        "description": "Nome del servizio dell'appuntamento da cancellare (opzionale)",
                    },
                    "data": {
                        "type": "string",
                        "description": "Data dell'appuntamento da cancellare YYYY-MM-DD (opzionale)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sposta_appuntamento",
            "description": (
                "Sposta un appuntamento attivo del cliente a una nuova data e ora. "
                "Se il cliente ha piu' appuntamenti, specifica servizio e/o data_attuale per disambiguare."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "servizio": {
                        "type": "string",
                        "description": "Nome del servizio dell'appuntamento da spostare (opzionale)",
                    },
                    "data_attuale": {
                        "type": "string",
                        "description": "Data attuale dell'appuntamento da spostare YYYY-MM-DD (opzionale)",
                    },
                    "nuova_data": {"type": "string", "description": "Nuova data YYYY-MM-DD"},
                    "nuova_ora": {"type": "string", "description": "Nuovo orario HH:MM"},
                },
                "required": ["nuova_data", "nuova_ora"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cerca_appuntamenti",
            "description": "Mostra gli appuntamenti futuri del cliente.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# Mappa nome tool → funzione (rispondi_paziente gestito a parte)
TOOL_DISPATCH = {
    "registra_paziente": tools.registra_paziente,
    "lista_servizi": tools.lista_servizi,
    "cerca_disponibilita": tools.cerca_disponibilita,
    "prenota_appuntamento": tools.prenota_appuntamento,
    "cancella_appuntamento": tools.cancella_appuntamento,
    "sposta_appuntamento": tools.sposta_appuntamento,
    "cerca_appuntamenti": tools.cerca_appuntamenti,
}


# ---------- System prompt ----------

def _build_system_prompt(db: Session, paziente: Paziente) -> str:
    """Costruisce il system prompt con info studio e stato paziente."""
    studio = db.query(Studio).first()
    nome_studio = studio.nome if studio else "Salone"
    telefono = studio.telefono if studio else ""
    orari = f"{studio.orario_apertura}-{studio.orario_chiusura}" if studio else "09:00-18:00"
    giorni = studio.giorni_lavorativi if studio else "lun-ven"

    # Stato cliente
    if paziente.nome == "Nuovo" and paziente.cognome == "Paziente":
        stato_paz = "CLIENTE NON REGISTRATO (nome sconosciuto). Chiedi il nome SOLO al momento di confermare una prenotazione."
    else:
        stato_paz = f"Cliente: {paziente.nome} {paziente.cognome} (tel: {paziente.telefono})"

    # Appuntamenti attivi
    appuntamenti = (
        db.query(Appuntamento)
        .filter(
            Appuntamento.paziente_id == paziente.id,
            Appuntamento.stato.in_([StatoAppuntamento.PRENOTATO, StatoAppuntamento.CONFERMATO]),
            Appuntamento.data_ora > datetime.now(),
        )
        .order_by(Appuntamento.data_ora)
        .limit(10)
        .all()
    )
    if appuntamenti:
        app_info = []
        for a in appuntamenti:
            srv = db.query(Servizio).get(a.servizio_id)
            nome_srv = srv.nome if srv else "N/A"
            app_info.append(f"  - {nome_srv} il {a.data_ora.strftime('%d/%m/%Y alle %H:%M')}")
        app_str = "Appuntamenti futuri:\n" + "\n".join(app_info)
    else:
        app_str = "Nessun appuntamento futuro."

    # Servizi disponibili (con descrizione triage se presente)
    servizi = db.query(Servizio).all()
    if servizi:
        srv_info = []
        for s in servizi:
            line = f"  - {s.nome} ({s.durata_minuti} min, €{s.prezzo})"
            if s.descrizione_triage:
                line += f"\n    -> Quando proporlo: {s.descrizione_triage}"
            srv_info.append(line)
        srv_str = "Servizi disponibili:\n" + "\n".join(srv_info)
    else:
        srv_str = "Nessun servizio configurato."

    now_dt = datetime.now()
    now = now_dt.strftime("%Y-%m-%d %H:%M")
    oggi_giorno = _GIORNI_IT[now_dt.weekday()]

    return (
        f'Sei l\'assistente del salone "{nome_studio}".\n'
        f"Telefono: {telefono}\n"
        f"Orari: {orari}, giorni: {giorni}\n"
        f"Oggi: {oggi_giorno} {now}\n\n"
        f"{stato_paz}\n"
        f"{app_str}\n\n"
        f"{srv_str}\n\n"
        "REGOLE GENERALI:\n"
        "- Rispondi SEMPRE in italiano, in modo cordiale e informale (dai del tu).\n"
        "- Risposte brevi e chiare.\n"
        "- Puoi chiamare PIU' FUNZIONI contemporaneamente nello stesso turno.\n\n"

        "QUANDO CHIAMARE OGNI FUNZIONE:\n\n"

        "rispondi_paziente(messaggio):\n"
        "  Usa per: saluti, domande generiche, chiedere chiarimenti.\n"
        "  Usa ANCHE quando ti manca un'informazione per procedere.\n"
        "  Il tuo messaggio finira' nella cronologia: al turno successivo vedrai la risposta\n"
        "  del cliente e potrai chiamare le funzioni operative.\n\n"

        "registra_paziente(nome, cognome):\n"
        "  Chiama SOLO quando il cliente dice ESPLICITAMENTE il suo nome (es. 'Sono Mario Rossi').\n"
        "  NON inventare nomi. NON usare nomi dagli esempi.\n"
        "  Frasi come 'lunedi 12 e 30', 'domani alle 10', 'quello delle 16' NON sono nomi: sono scelte di orario.\n\n"

        "cerca_disponibilita(servizio, data?):\n"
        "  Il parametro 'servizio' e' OBBLIGATORIO. NON chiamare senza servizio.\n"
        "  Se il cliente non ha specificato il servizio, chiedi con rispondi_paziente.\n"
        "  Se non ha specificato la data, ometti il parametro data (verranno mostrati i prossimi giorni).\n"
        "  NON cercare disponibilita' per servizi che non esistono nella lista sopra.\n\n"

        "prenota_appuntamento(servizio, data, ora):\n"
        "  Chiama SOLO dopo che il cliente ha CONFERMATO esplicitamente.\n"
        "  FLUSSO OBBLIGATORIO:\n"
        "  1. cerca_disponibilita mostra gli orari disponibili\n"
        "  2. Il cliente sceglie un orario → chiedi conferma con rispondi_paziente\n"
        "  3. Il cliente conferma (si, ok, va bene) → ORA chiami prenota_appuntamento\n"
        "  NON chiamare prenota_appuntamento quando il cliente sceglie un orario. PRIMA chiedi conferma.\n"
        "  Se il cliente NON e' registrato (vedi 'NON REGISTRATO'), chiedi anche il nome nella conferma.\n"
        "  Se il cliente e' GIA' registrato, NON chiedere di nuovo il nome.\n\n"

        "cancella_appuntamento(servizio?, data?):\n"
        "  Chiama SOLO quando il cliente chiede ESPLICITAMENTE di cancellare un appuntamento.\n"
        "  Se ha PIU' appuntamenti futuri e specifica quale, includi servizio e/o data.\n"
        "  Se non specifica, chiama senza parametri: il sistema chiedera' chiarimenti.\n\n"

        "sposta_appuntamento(nuova_data, nuova_ora, servizio?, data_attuale?):\n"
        "  Chiama SOLO quando il cliente chiede di spostare un appuntamento GIA' PRENOTATO.\n"
        "  NON usare quando sta scegliendo un orario per una NUOVA prenotazione.\n"
        "  Se indica la nuova data MA NON l'ora, chiama prima cerca_disponibilita.\n"
        "  Chiama sposta_appuntamento SOLO quando hai sia nuova_data che nuova_ora confermati.\n\n"

        "lista_servizi():\n"
        "  Chiama quando il cliente chiede servizi o prezzi.\n\n"

        "cerca_appuntamenti():\n"
        "  Chiama quando il cliente chiede i suoi appuntamenti futuri.\n\n"

        "PRIORITA' NELLA CONVERSAZIONE:\n"
        "  1. Servizio → se manca, chiedi quale servizio\n"
        "  2. Data/ora → cerca disponibilita, proponi orari\n"
        "  3. Nome → chiedi SOLO quando sei pronto a confermare la prenotazione\n"
        "  Se il cliente lo dice spontaneamente, registralo SUBITO con registra_paziente.\n\n"

        "INTERPRETAZIONE RISPOSTE BREVI:\n"
        "  Quando il cliente risponde con frasi brevi dopo aver visto gli orari disponibili,\n"
        "  sono SEMPRE scelte di orario dalla lista. NON sono nomi di persona.\n"
        "  REGOLA CRITICA: Se il turno precedente mostrava una lista di orari per giorno:\n"
        "  - 'Martedi 9' = martedi alle 09:00 (NON il giorno 9 del mese!)\n"
        "  - 'Lunedi 12 e 30' = lunedi alle 12:30\n"
        "  - 'Quello delle 16' = l'orario delle 16:00 dalla lista\n"
        "  - 'Il primo' = il primo orario della lista\n"
        "  In questi casi chiedi conferma con rispondi_paziente, NON chiamare cerca_disponibilita.\n\n"

        "ESEMPI:\n\n"

        "1) Cliente: 'Ciao, avete posto domani?'\n"
        "   → Manca il servizio. Chiama:\n"
        "   rispondi_paziente(messaggio='Ciao! Per cosa? Taglio, barba, o taglio e barba?')\n\n"

        "2) Cliente (dopo esempio 1): 'Taglio e barba'\n"
        "   → Dalla cronologia sai che vuole domani. Chiama:\n"
        "   cerca_disponibilita(servizio='Taglio e barba', data='2026-03-01')\n\n"

        "3) Cliente (dopo aver visto gli orari): 'Martedi 9' oppure 'Quello delle 16'\n"
        "   → Sta SCEGLIENDO un orario dalla lista. Interpreta:\n"
        "     'Martedi 9' = martedi alle 09:00.\n"
        "   → NON chiamare cerca_disponibilita! Chiedi conferma con riepilogo:\n"
        "   → Cliente NON registrato:\n"
        "   rispondi_paziente(messaggio='Taglio e barba martedi 02/03 alle 09:00 (45 min, €25). Confermi? Mi serve anche il tuo nome.')\n"
        "   → Cliente GIA' registrato:\n"
        "   rispondi_paziente(messaggio='Taglio e barba martedi 02/03 alle 09:00 (45 min, €25). Confermi?')\n\n"

        "4) Cliente registrato: 'Si confermo'\n"
        "   → Prenota:\n"
        "   prenota_appuntamento(servizio='Taglio e barba', data='2026-03-02', ora='09:00')\n\n"

        "5) Cliente non registrato: 'Si, sono Marco Rossi'\n"
        "   → Registra e prenota (usa il nome detto, MAI inventare):\n"
        "   registra_paziente(nome='Marco', cognome='Rossi') + prenota_appuntamento(servizio='Taglio e barba', data='2026-03-02', ora='09:00')\n\n"

        "6) Cliente: 'Taglio domani alle 10'\n"
        "   → Ha detto tutto, ma verifica disponibilita':\n"
        "   cerca_disponibilita(servizio='Taglio uomo', data='...')\n\n"

        "7) Cliente: 'Vorrei prenotare'\n"
        "   → Manca il servizio. Chiama:\n"
        "   rispondi_paziente(messaggio='Ciao! Cosa ti serve? Taglio, barba, o taglio e barba?')"
    )


def _get_conversazione_recente(db: Session, paziente_id: int) -> list[dict]:
    """Recupera ultimi messaggi come lista di messaggi OpenAI."""
    messaggi = (
        db.query(MessaggioLog)
        .filter(MessaggioLog.paziente_id == paziente_id)
        .order_by(MessaggioLog.timestamp.desc())
        .limit(10)
        .all()
    )
    result = []
    for m in reversed(messaggi):
        role = "user" if m.direzione == DirezioneMessaggio.IN else "assistant"
        result.append({"role": role, "content": m.testo[:300]})
    return result


# ---------- Helpers ----------

def _trova_o_crea_paziente(db: Session, telefono: str) -> Paziente:
    """Trova un paziente per telefono o ne crea uno nuovo."""
    paziente = db.query(Paziente).filter(Paziente.telefono == telefono).first()
    if not paziente:
        paziente = Paziente(
            nome="Nuovo",
            cognome="Paziente",
            telefono=telefono,
            whatsapp_id=telefono.lstrip("+"),
        )
        db.add(paziente)
        db.commit()
        db.refresh(paziente)
        logger.info("  -> Nuovo paziente creato (ID: %d)", paziente.id)
    return paziente


def _logga_messaggio(db: Session, paziente_id: int | None,
                     direzione: DirezioneMessaggio, testo: str,
                     wa_message_id: str | None = None):
    """Salva un messaggio nel log."""
    msg = MessaggioLog(
        paziente_id=paziente_id,
        direzione=direzione,
        testo=testo,
        whatsapp_message_id=wa_message_id,
    )
    db.add(msg)
    db.commit()


# ---------- Risposta deterministica ----------

_GIORNI_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def _formatta_data(data_str: str) -> str:
    """Formatta YYYY-MM-DD in 'giorno DD/MM/YYYY' italiano."""
    try:
        dt = datetime.strptime(data_str, "%Y-%m-%d")
        return f"{_GIORNI_IT[dt.weekday()]} {dt.strftime('%d/%m/%Y')}"
    except ValueError:
        return data_str


def _formatta_risposta(
    risultati: dict[str, dict],
    risposta_diretta: str | None,
    nome_paziente: str = "",
    primo_messaggio: bool = False,
    nome_dottore: str = "",
    nome_studio: str = "",
    indirizzo_studio: str = "",
) -> str:
    """Formatta risposta deterministica basata sui risultati dei tool.

    Nessuna seconda chiamata GPT: la risposta è costruita
    direttamente dai dati restituiti dai tool.
    """
    parti = []

    # Saluto iniziale (solo al primo messaggio della conversazione)
    if primo_messaggio:
        if nome_paziente:
            parti.append(f"Buongiorno {nome_paziente}!")
        else:
            parti.append("Buongiorno!")

    # Nota registrazione (side-effect, prepend se ci sono altri tool)
    if "registra_paziente" in risultati:
        res = risultati["registra_paziente"]
        if res.get("ok") and len(risultati) > 1:
            parti.append(f"Perfetto {res['nome_completo']}!")

    # --- PRIORITÀ 1: prenota_appuntamento ---
    if "prenota_appuntamento" in risultati:
        res = risultati["prenota_appuntamento"]
        if res.get("ok"):
            app = res["appuntamento"]
            prezzo_info = f" — costo €{app['prezzo_euro']:.0f}" if app.get("prezzo_euro") else ""
            parti.append("Prenotazione confermata!")
            parti.append(
                f"{app['servizio']} il {app['data']} alle {app['ora']}"
                f" ({app['durata_minuti']} minuti{prezzo_info})."
            )
            if nome_dottore:
                luogo = f"presso {nome_studio}, {indirizzo_studio}" if indirizzo_studio else f"presso {nome_studio}"
                parti.append(f"Con {nome_dottore} {luogo}.")
            parti.append("A presto!")
        else:
            if res.get("errore") == "nome_richiesto":
                parti.append("Per confermare la prenotazione, avrei bisogno del suo nome e cognome.")
            else:
                errore = res.get("messaggio", res.get("errore", "Errore nella prenotazione"))
                parti.append(f"Non è stato possibile prenotare: {errore}")
        return "\n".join(parti)

    # --- PRIORITÀ 2: cancella_appuntamento ---
    if "cancella_appuntamento" in risultati:
        res = risultati["cancella_appuntamento"]
        if res.get("ok"):
            c = res["cancellato"]
            parti.append(f"Va bene, ho cancellato l'appuntamento di {c['servizio']} del {c['data']} alle {c['ora']}.")
        elif res.get("scelta_richiesta"):
            parti.append("Ha più appuntamenti futuri:")
            for a in res["appuntamenti"]:
                parti.append(f"- {a['servizio']} il {a['data']} alle {a['ora']}")
            parti.append("\nQuale desidera cancellare?")
        else:
            parti.append(res.get("errore", "Errore nella cancellazione."))
        return "\n".join(parti)

    # --- PRIORITÀ 3: sposta_appuntamento ---
    if "sposta_appuntamento" in risultati:
        res = risultati["sposta_appuntamento"]
        if res.get("ok"):
            sp = res["spostato"]
            parti.append(f"Ho spostato l'appuntamento di {sp['servizio']} al {sp['nuova_data']} alle {sp['nuova_ora']}.")
        elif res.get("scelta_richiesta"):
            parti.append("Ha più appuntamenti futuri:")
            for a in res["appuntamenti"]:
                parti.append(f"- {a['servizio']} il {a['data']} alle {a['ora']}")
            parti.append("\nQuale desidera spostare?")
        else:
            parti.append(res.get("errore", "Errore nello spostamento."))
        return "\n".join(parti)

    # --- PRIORITÀ 4: cerca_disponibilita ---
    if "cerca_disponibilita" in risultati:
        res = risultati["cerca_disponibilita"]
        if res.get("errore"):
            parti.append(res.get("messaggio", res["errore"]))
        elif res.get("disponibilita"):
            servizio = res.get("servizio", "")
            durata = res.get("durata_minuti", 0)
            prezzo = res.get("prezzo_euro", 0)
            prezzo_str = f", €{prezzo:.0f}" if (prezzo and prezzo == int(prezzo)) else (f", €{prezzo:.2f}" if prezzo else "")
            # Intro caldo da GPT (es. riconoscimento sintomi)
            if risposta_diretta:
                parti.append(risposta_diretta.strip())
            # Nota su data non disponibile
            if res.get("nota"):
                parti.append(res["nota"] + ".")
            # Disponibilità con info servizio sempre visibili
            parti.append(f"Ecco le prossime disponibilità per {servizio} ({durata} minuti{prezzo_str}):")
            if nome_dottore:
                luogo = f"presso {nome_studio}, {indirizzo_studio}" if indirizzo_studio else f"presso {nome_studio}"
                parti.append(f"Con {nome_dottore} {luogo}.")
            for g in res["disponibilita"]:
                label = g.get("giorno") or _formatta_data(g["data"])
                orari_str = ", ".join(g["orari"])
                parti.append(f"- {label}: {orari_str}")
            parti.append("Quale orario preferisce?")
        else:
            if risposta_diretta:
                parti.append(risposta_diretta.strip())
            parti.append("Mi dispiace, non ho trovato disponibilità nei prossimi giorni. Vuole provare un'altra data?")
        return "\n".join(parti)

    # --- PRIORITÀ 5: lista_servizi ---
    if "lista_servizi" in risultati:
        res = risultati["lista_servizi"]
        servizi = res.get("servizi", [])
        if servizi:
            parti.append("Ecco i servizi che offriamo:")
            for s in servizi:
                parti.append(f"- {s['nome']} ({s['durata_minuti']} min, {s['prezzo_euro']}€)")
            parti.append("\nPer quale servizio desidera prenotare?")
        else:
            parti.append("Al momento non ci sono servizi configurati.")
        return "\n".join(parti)

    # --- PRIORITÀ 6: cerca_appuntamenti ---
    if "cerca_appuntamenti" in risultati:
        res = risultati["cerca_appuntamenti"]
        appuntamenti = res.get("appuntamenti", [])
        if appuntamenti:
            parti.append("I suoi prossimi appuntamenti:")
            for a in appuntamenti:
                parti.append(f"- {a['servizio']} il {a['data']} alle {a['ora']}")
        else:
            parti.append("Non ha appuntamenti futuri al momento.")
        return "\n".join(parti)

    # Solo registra_paziente (nessun altro tool)
    if "registra_paziente" in risultati:
        res = risultati["registra_paziente"]
        if res.get("ok"):
            return f"Piacere {res['nome_completo']}! Come posso aiutarla?"
        return "Non sono riuscito a registrare il nome. Può riprovare?"

    # Fallback
    if risposta_diretta:
        return risposta_diretta.strip()
    return "Come posso aiutarla?"


# ---------- Entry point ----------

async def processa_messaggio(
    telefono: str, testo: str, db: Session, wa_message_id: str | None = None
) -> str:
    """Processa un messaggio in arrivo da un paziente."""
    logger.info(SEP_HEAVY)
    logger.info("MESSAGGIO IN ARRIVO")
    logger.info("  Telefono: %s", telefono)
    logger.info("  Testo: \"%s\"", testo)

    # 1. Trova o crea paziente
    paziente = _trova_o_crea_paziente(db, telefono)
    logger.info("  Paziente: %s %s (ID: %d)", paziente.nome, paziente.cognome, paziente.id)
    logger.info(SEP_LIGHT)
    logger.info("ELABORAZIONE")

    # 2. Logga messaggio in arrivo
    _logga_messaggio(db, paziente.id, DirezioneMessaggio.IN, testo, wa_message_id)

    try:
        # 3. Esegui LLM scheduler
        risposta = _esegui_scheduler(telefono, testo, db, paziente)

    except Exception as e:
        logger.error("  ERRORE: %s", e, exc_info=True)
        risposta = (
            "Mi scusi, si è verificato un problema tecnico. "
            "La prego di riprovare tra qualche istante o contattare lo studio."
        )

    # 4. Logga risposta in uscita
    _logga_messaggio(db, paziente.id, DirezioneMessaggio.OUT, risposta)

    logger.info(SEP_LIGHT)
    logger.info("RISPOSTA")
    for line in risposta.split("\n"):
        logger.info("  %s", line)
    logger.info(SEP_HEAVY)

    return risposta


def _esegui_scheduler(telefono: str, testo: str, db: Session, paziente: Paziente) -> str:
    """Esegue il ciclo LLM scheduler: tools → risultati → risposta."""
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY mancante")
        return "Mi scusi, il servizio non e' disponibile al momento. Contatti lo studio direttamente."

    client = OpenAI(api_key=OPENAI_API_KEY)

    # System prompt + conversazione recente + messaggio corrente
    system_prompt = _build_system_prompt(db, paziente)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_get_conversazione_recente(db, paziente.id))
    messages.append({"role": "user", "content": testo})

    # Step 1: LLM decide quali tools chiamare (obbligatorio)
    logger.info("  -> Scheduler: invio a GPT con %d tools (required)", len(TOOLS_SCHEMA))
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=messages,
        tools=TOOLS_SCHEMA,
        tool_choice="required",
        reasoning_effort="low",
        max_completion_tokens=2048,
    )

    assistant_msg = response.choices[0].message
    tool_calls = assistant_msg.tool_calls or []
    logger.info("  -> Scheduler: %d tool calls", len(tool_calls))

    # Fallback: se GPT non ha chiamato nessun tool (non dovrebbe succedere con required)
    if not tool_calls:
        return (assistant_msg.content or "Come posso aiutarla?").strip()

    # Step 2: Esegui tutti i tool calls
    risposta_diretta = None
    risultati = {}  # func_name → result (per formattazione deterministica)

    for tool_call in tool_calls:
        func_name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            args = {}

        logger.info("  -> Tool: %s(%s)", func_name, args)

        if func_name == "rispondi_paziente":
            risposta_diretta = args.get("messaggio", "")
            logger.info("  -> rispondi_paziente: messaggio diretto")
            continue

        func = TOOL_DISPATCH.get(func_name)
        if func:
            try:
                result = func(db=db, telefono=telefono, **args)
            except Exception as e:
                logger.error("  -> Errore tool %s: %s", func_name, e)
                result = {"errore": str(e)}
        else:
            result = {"errore": f"Funzione '{func_name}' non trovata"}

        risultati[func_name] = result
        logger.info("  -> Risultato: %s", json.dumps(result, ensure_ascii=False)[:200])

    # Step 3: Risposta deterministica (no seconda chiamata GPT)
    if not risultati:
        # Solo rispondi_paziente (o nessun tool valido)
        return (risposta_diretta or "Come posso aiutarla?").strip()

    # Se registra_paziente è l'unico tool business e c'è rispondi_paziente,
    # usa il messaggio GPT (es. "Piacere Marco! Per quale servizio?")
    if set(risultati.keys()) == {"registra_paziente"} and risposta_diretta:
        return risposta_diretta.strip()

    # Determina se è il primo messaggio della conversazione
    conversazione = _get_conversazione_recente(db, paziente.id)
    # conversazione include il messaggio corrente appena loggato, quindi <= 2 (1 IN + 1 OUT max)
    primo_msg = len(conversazione) <= 2

    # Nome paziente (se registrato)
    nome_paz = ""
    if paziente.nome != "Nuovo" or paziente.cognome != "Paziente":
        nome_paz = f"{paziente.nome} {paziente.cognome}"
    # Se appena registrato in questo turno, usa il nome dal risultato
    if "registra_paziente" in risultati and risultati["registra_paziente"].get("ok"):
        nome_paz = risultati["registra_paziente"]["nome_completo"]

    # Info studio per dottore e luogo nelle risposte
    studio = db.query(Studio).first()
    risposta = _formatta_risposta(
        risultati, risposta_diretta, nome_paz, primo_msg,
        nome_dottore=studio.nome_dottore or "" if studio else "",
        nome_studio=studio.nome or "" if studio else "",
        indirizzo_studio=studio.indirizzo or "" if studio else "",
    )
    logger.info("  -> Risposta deterministica generata")
    return risposta


# Mantenuta per compatibilita con dashboard/API (routers/api.py)
def crea_appuntamento_da_conferma(
    db: Session, telefono: str, servizio_nome: str,
    data_str: str, ora_str: str
) -> Appuntamento | None:
    """Crea un appuntamento - usato dalla dashboard/API."""
    paziente = db.query(Paziente).filter(Paziente.telefono == telefono).first()
    if not paziente:
        return None

    servizio = db.query(Servizio).filter(Servizio.nome.ilike(f"%{servizio_nome}%")).first()
    if not servizio:
        return None

    data_ora = datetime.strptime(f"{data_str} {ora_str}", "%Y-%m-%d %H:%M")

    titolo = f"{servizio.nome} - {paziente.nome} {paziente.cognome}"
    descrizione = f"Paziente: {paziente.nome} {paziente.cognome}\nTel: {paziente.telefono}"
    event_id = calendar_sync.crea_evento(titolo, data_ora, servizio.durata_minuti, descrizione)

    appuntamento = Appuntamento(
        paziente_id=paziente.id,
        servizio_id=servizio.id,
        data_ora=data_ora,
        durata_minuti=servizio.durata_minuti,
        stato=StatoAppuntamento.PRENOTATO,
        google_event_id=event_id,
    )
    db.add(appuntamento)
    db.commit()
    db.refresh(appuntamento)
    return appuntamento
