"""Prompt di sistema tenuti in codice (non in dashboard). I file sono in /prompts.

- voce_admin: usato quando chiama un amministratore (vedi services/promemoria.is_admin):
  l'assistente NON tratta l'admin come un cliente e lo aiuta a lasciare promemoria.
"""

import os

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_BASE, "prompts")

_ADMIN_FALLBACK = (
    "Stai parlando con l'AMMINISTRATORE, non con un cliente. Non registrarlo in anagrafica e non "
    "aprire ticket per lui. Aiutalo a lasciare promemoria per i clienti con lo strumento "
    "lascia_promemoria (nome cliente, testo, giorni di validità). Per gli strumenti che richiedono "
    "'telefono' usa {{telefono_chiamante}}."
)


def _leggi(nome: str, fallback: str = "") -> str:
    try:
        with open(os.path.join(_DIR, nome), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return fallback


def voce_admin() -> str:
    return _leggi("voce_admin.txt", _ADMIN_FALLBACK)
