"""Salvataggio su disco dei documenti caricati (base di conoscenza aziendale).

L'indicizzazione (estrazione pagina-per-pagina + sezionamento) vive in
services/ingestion.py: qui ci occupiamo solo di persistere il file originale,
che va sempre conservato.
"""

import logging
import os
import re

from database import Documento, StatoDocumento
from services import email as email_service

CATEGORIE_DOC = ["listino", "schede_prodotto", "contratti", "faq", "altro"]


def catalogo_prompt(db) -> str:
    """Blocco da iniettare nel prompt: elenco dei documenti DISPONIBILI per categoria, con un
    breve summary. Serve all'assistente per sapere cosa può allegare/citare e cosa no."""
    docs = (db.query(Documento)
            .filter(Documento.stato.in_([StatoDocumento.READY, StatoDocumento.NEEDS_REVIEW]))
            .order_by(Documento.categoria, Documento.caricato_at.desc()).all())
    if not docs:
        return ""
    per_cat: dict[str, list] = {}
    for d in docs:
        per_cat.setdefault(d.categoria, []).append(d)
    righe = []
    for cat in CATEGORIE_DOC:
        lst = per_cat.get(cat)
        if not lst:
            continue
        righe.append(f"Categoria «{cat}»:")
        for d in lst:
            summ = next((s.summary for s in sorted(d.sezioni, key=lambda s: s.ordine) if s.summary), None)
            summ = (summ or "").strip().replace("\n", " ")
            righe.append(f"  - {d.nome_file}: {summ[:200] if summ else '(contenuto del file)'}")
    return (
        "\n\n=== DOCUMENTI DISPONIBILI (cosa puoi allegare via email) ===\n"
        "Puoi allegare SOLO i documenti elencati qui sotto, indicando la loro categoria. "
        "Le categorie NON elencate non hanno documenti: non provare ad allegarle.\n"
        + "\n".join(righe)
    )


def invia_mail_contatto(db, contatto, testo: str, oggetto: str = "", categoria_allegato: str = "",
                        nome_azienda: str = "") -> dict:
    """Invia un'email a testo libero al contatto, con allegato OPZIONALE (i documenti di una
    categoria). Ritorna esito; se la categoria richiesta è vuota lo segnala (ma invia il testo)."""
    email = (contatto.email or "").strip()
    if not email:
        return {"email_mancante": True,
                "messaggio": "Il cliente non ha un'email salvata: chiedigliela, salvala e riprova."}
    if not (testo or "").strip():
        return {"errore": "Testo della mail mancante: scrivi tu il corpo del messaggio."}
    allegati, nomi, allegato_mancante = [], [], False
    cat = (categoria_allegato or "").strip()
    if cat:
        docs = (db.query(Documento)
                .filter(Documento.categoria == cat,
                        Documento.stato.in_([StatoDocumento.READY, StatoDocumento.NEEDS_REVIEW])).all())
        att = [(d.nome_file, d.percorso) for d in docs if d.percorso and os.path.exists(d.percorso)]
        if att:
            nomi = [n for n, _ in att]
            allegati = [p for _, p in att]
        else:
            allegato_mancante = True
    oggetto = (oggetto or "").strip() or (nome_azienda or "Informazioni")
    inviata = email_service.invia_email(destinatario=email, oggetto=oggetto,
                                        corpo=testo.strip(), allegati=allegati or None)
    if not inviata:
        return {"errore": "Invio email non riuscito (verifica la configurazione Gmail)."}
    return {"inviato": True, "email": email, "allegati": nomi,
            "allegato_richiesto_non_trovato": allegato_mancante}


def invia_documento_email(db, email: str, documento_id: int, testo: str = "", oggetto: str = "",
                          nome_azienda: str = "") -> dict:
    """Invia via email UNO specifico documento (per id) come allegato. Destinatario = `email`
    (Margherita la conosce o la chiede). Invia SOLO se il documento è marcato `inviabile`."""
    email = (email or "").strip()
    if not email:
        return {"email_mancante": True,
                "messaggio": "Manca l'email del destinatario: chiedila al cliente e riprova."}
    doc = db.get(Documento, int(documento_id)) if documento_id else None
    if not doc:
        return {"errore": "Documento non trovato (documento_id errato)."}
    if not doc.inviabile:
        return {"non_inviabile": True,
                "messaggio": f"Il documento «{doc.nome_file}» non è inviabile ai clienti. Non inviarlo."}
    if not (doc.percorso and os.path.exists(doc.percorso)):
        return {"errore": f"Il file «{doc.nome_file}» non è al momento disponibile sul server."}
    corpo = (testo or "").strip() or f"Gentile cliente,\nin allegato {doc.nome_file}.\n\n{nome_azienda or ''}".strip()
    oggetto = (oggetto or "").strip() or (nome_azienda or "Documento")
    inviata = email_service.invia_email(destinatario=email, oggetto=oggetto, corpo=corpo,
                                        allegati=[doc.percorso])
    if not inviata:
        return {"errore": "Invio email non riuscito (verifica la configurazione Gmail)."}
    return {"inviato": True, "email": email, "documento": doc.nome_file}

logger = logging.getLogger(__name__)

BASE_DIR = os.getenv("DOCUMENTI_DIR", "data/documenti")

# Categorie note (allineate a routers/dashboard.CATEGORIE_DOCUMENTI).
CATEGORIE_KEYS = ["listino", "schede_prodotto", "contratti", "faq", "altro"]

ESTENSIONI_AMMESSE = (".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx", ".xls")

# Formati tabellari: caricati per intero, senza chiamata LLM (niente sezionamento).
ESTENSIONI_TABELLARI = (".csv", ".xlsx", ".xls")


def _safe_filename(nome: str) -> str:
    """Ripulisce il nome file da percorsi e caratteri pericolosi."""
    nome = os.path.basename(nome or "documento")
    nome = re.sub(r"[^A-Za-z0-9._-]", "_", nome)
    return nome[:200] or "documento"


def salva_documento(owner_id: int, nome_file: str, content: bytes) -> dict:
    """Salva il file su disco. Ritorna metadati (no estrazione qui).

    Solleva ValueError per estensioni non ammesse o file vuoti.
    """
    if not content:
        raise ValueError("Il file è vuoto.")

    nome_pulito = _safe_filename(nome_file)
    ext = os.path.splitext(nome_pulito)[1].lower()
    if ext not in ESTENSIONI_AMMESSE:
        raise ValueError(f"Formato non supportato ({ext or 'sconosciuto'}). Ammessi: PDF, DOCX, TXT.")

    cartella = os.path.join(BASE_DIR, str(owner_id))
    os.makedirs(cartella, exist_ok=True)

    percorso = os.path.join(cartella, nome_pulito)
    base, estensione = os.path.splitext(percorso)
    i = 1
    while os.path.exists(percorso):
        percorso = f"{base}_{i}{estensione}"
        i += 1

    with open(percorso, "wb") as f:
        f.write(content)

    return {
        "nome_file": nome_pulito,
        "percorso": percorso,
        "dimensione": len(content),
    }


def estrai_testo_semplice(percorso: str) -> str:
    """Estrae il testo grezzo da DOCX/TXT/MD/CSV/XLSX (per i documenti non-PDF).

    CSV ed Excel vengono caricati per intero (tutte le righe, tutti i fogli),
    senza alcuna chiamata LLM. I PDF passano invece dalla pipeline di ingestion
    (pdftotext + sezionatore).
    """
    ext = os.path.splitext(percorso)[1].lower()
    try:
        if ext == ".docx":
            import docx
            d = docx.Document(percorso)
            return "\n".join(p.text for p in d.paragraphs).strip()
        if ext in (".txt", ".md", ".csv"):
            with open(percorso, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        if ext in (".xlsx", ".xls"):
            return _estrai_excel(percorso)
    except Exception as e:
        logger.error("Estrazione testo semplice fallita per %s: %s", percorso, e)
    return ""


def _estrai_excel(percorso: str) -> str:
    """Dump testuale integrale di un Excel: ogni foglio, tutte le righe (formato CSV).

    Nessuna chiamata LLM: il contenuto viene conservato per intero così com'è.
    """
    import pandas as pd

    fogli = pd.read_excel(percorso, sheet_name=None, dtype=str)
    blocchi = []
    for nome_foglio, df in fogli.items():
        df = df.fillna("")
        corpo = df.to_csv(index=False).strip()
        blocchi.append(f"===== Foglio: {nome_foglio} =====\n{corpo}")
    return "\n\n".join(blocchi).strip()


def invia_documenti_email(db, contatto, categoria: str, nome_azienda: str) -> dict:
    """Invia al contatto, via email, i documenti caricati nella `categoria` indicata.

    Ritorna un dict di esito (per i risponditori):
      {"inviato": True, "email": ..., "documenti": [...]}
      oppure {"email_mancante": True, "messaggio": ...} se il contatto non ha email
      oppure {"errore": ...}.
    """
    cat = (categoria or "").strip()
    docs = (db.query(Documento).filter(Documento.categoria == cat)
            .order_by(Documento.caricato_at.desc()).all())
    allegati = [(d.nome_file, d.percorso) for d in docs if d.percorso and os.path.exists(d.percorso)]
    if not allegati:
        return {"errore": f"Nessun documento disponibile nella categoria '{cat}'."}

    email = (contatto.email or "").strip()
    if not email:
        return {"email_mancante": True,
                "messaggio": "Il cliente non ha un'email salvata: chiedigliela, salvala con "
                             "salva_contatto e riprova."}

    nomi = [n for n, _ in allegati]
    oggetto = f"Documenti richiesti - {nome_azienda}"
    corpo = (f"Gentile {contatto.nome or contatto.nome_completo},\n\n"
             f"in allegato i documenti richiesti: {', '.join(nomi)}.\n\n"
             f"Cordiali saluti,\n{nome_azienda}")
    inviata = email_service.invia_email(destinatario=email, oggetto=oggetto, corpo=corpo,
                                        allegati=[p for _, p in allegati])
    if not inviata:
        return {"errore": "Invio email non riuscito (verifica la configurazione Gmail)."}
    return {"inviato": True, "email": email, "documenti": nomi}


def elimina_file(percorso: str) -> None:
    """Rimuove il file da disco (silenzioso se non esiste)."""
    try:
        if percorso and os.path.exists(percorso):
            os.remove(percorso)
    except OSError as e:
        logger.warning("Impossibile rimuovere %s: %s", percorso, e)
