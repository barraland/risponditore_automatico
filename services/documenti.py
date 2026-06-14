"""Salvataggio su disco dei documenti caricati per i condomìni.

L'indicizzazione (estrazione pagina-per-pagina + sezionamento) vive in
services/ingestion.py: qui ci occupiamo solo di persistere il file originale,
che va sempre conservato.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

BASE_DIR = os.getenv("DOCUMENTI_DIR", "data/documenti")

ESTENSIONI_AMMESSE = (".pdf", ".docx", ".txt", ".md")


def _safe_filename(nome: str) -> str:
    """Ripulisce il nome file da percorsi e caratteri pericolosi."""
    nome = os.path.basename(nome or "documento")
    nome = re.sub(r"[^A-Za-z0-9._-]", "_", nome)
    return nome[:200] or "documento"


def salva_documento(condominio_id: int, nome_file: str, content: bytes) -> dict:
    """Salva il file su disco. Ritorna metadati (no estrazione qui).

    Solleva ValueError per estensioni non ammesse o file vuoti.
    """
    if not content:
        raise ValueError("Il file è vuoto.")

    nome_pulito = _safe_filename(nome_file)
    ext = os.path.splitext(nome_pulito)[1].lower()
    if ext not in ESTENSIONI_AMMESSE:
        raise ValueError(f"Formato non supportato ({ext or 'sconosciuto'}). Ammessi: PDF, DOCX, TXT.")

    cartella = os.path.join(BASE_DIR, str(condominio_id))
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
    """Estrae il testo grezzo da DOCX/TXT/MD (per i documenti non-PDF).

    I PDF passano dalla pipeline di ingestion (pdftotext + sezionatore).
    """
    ext = os.path.splitext(percorso)[1].lower()
    try:
        if ext == ".docx":
            import docx
            d = docx.Document(percorso)
            return "\n".join(p.text for p in d.paragraphs).strip()
        if ext in (".txt", ".md"):
            with open(percorso, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
    except Exception as e:
        logger.error("Estrazione testo semplice fallita per %s: %s", percorso, e)
    return ""


def elimina_file(percorso: str) -> None:
    """Rimuove il file da disco (silenzioso se non esiste)."""
    try:
        if percorso and os.path.exists(percorso):
            os.remove(percorso)
    except OSError as e:
        logger.warning("Impossibile rimuovere %s: %s", percorso, e)
