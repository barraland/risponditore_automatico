"""Persistenza e riassunto delle telefonate (log chiamate vocali).

A fine chiamata salviamo la trascrizione completa (dialogo chiamante/assistente)
e un breve riassunto generato via LLM, collegati al contatto.
"""

import logging
import os

from openai import OpenAI
from sqlalchemy.orm import Session

from database import ChiamataVoce

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("VOICE_LOG_MODEL", "gpt-5-mini")

RIASSUNTO_SYSTEM = (
    "Riassumi questa telefonata tra un potenziale cliente (lead) e l'assistente vocale "
    "dell'azienda, in 2-4 frasi: cosa ha chiesto il lead, cosa è stato risposto, quali dati "
    "sono stati raccolti e se è rimasto qualcosa in sospeso (es. richiamo del commerciale). "
    "Tono neutro e sintetico."
)


def _formatta_trascrizione(turni: list[dict]) -> str:
    """turni: [{'ruolo': 'Cliente'|'Assistente', 'testo': str}, ...]"""
    return "\n".join(f"{t['ruolo']}: {t['testo'].strip()}" for t in turni if t.get("testo", "").strip())


def riassumi(trascrizione: str) -> str | None:
    if not OPENAI_API_KEY or not trascrizione.strip():
        return None
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": RIASSUNTO_SYSTEM},
                {"role": "user", "content": trascrizione},
            ],
            reasoning_effort="low",
            max_completion_tokens=400,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as e:
        logger.error("Riassunto chiamata fallito: %s", e)
        return None


def salva_chiamata(db: Session, contatto_id: int, telefono: str | None,
                   turni: list[dict], iniziata_at, durata_sec: int | None = None) -> None:
    """Crea il record ChiamataVoce con trascrizione + riassunto. Non solleva."""
    try:
        trascrizione = _formatta_trascrizione(turni)
        if not trascrizione:
            return
        riassunto = riassumi(trascrizione)
        db.add(ChiamataVoce(
            contatto_id=contatto_id,
            telefono=telefono,
            iniziata_at=iniziata_at,
            durata_sec=durata_sec,
            trascrizione=trascrizione,
            riassunto=riassunto,
        ))
        db.commit()
        logger.info("Chiamata salvata per contatto %s (%d turni)", contatto_id, len(turni))
    except Exception as e:
        logger.error("Salvataggio chiamata fallito: %s", e)
        db.rollback()
