"""Salvataggio su disco dei documenti caricati (base di conoscenza aziendale).

L'indicizzazione (estrazione pagina-per-pagina + sezionamento) vive in
services/ingestion.py: qui ci occupiamo solo di persistere il file originale,
che va sempre conservato.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

BASE_DIR = os.getenv("DOCUMENTI_DIR", "data/documenti")

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


def elimina_file(percorso: str) -> None:
    """Rimuove il file da disco (silenzioso se non esiste)."""
    try:
        if percorso and os.path.exists(percorso):
            os.remove(percorso)
    except OSError as e:
        logger.warning("Impossibile rimuovere %s: %s", percorso, e)
