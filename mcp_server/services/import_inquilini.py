"""Import inquilini da un Excel/CSV destrutturato.

L'amministratore carica un file che NON ha uno schema fisso (colonne in ordine
qualsiasi, intestazioni assenti o in italiano corrente, dati sporchi). Lo
trasformiamo in testo grezzo e lasciamo a GPT-5-mini il compito di riconoscere
nome, cognome, unità, millesimi, telefono ed email per ogni riga.

Restituisce una lista di dict pronti per creare oggetti Inquilino.
"""

import io
import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_IMPORT_MODEL", "gpt-5-mini")

# Quante righe/testo passare al modello: sono pochi token, ma mettiamo un tetto
# di sicurezza per non far esplodere file giganti.
MAX_CHARS = 20000

SYSTEM_PROMPT = """Sei un assistente che struttura anagrafiche di condòmini.
Ricevi il contenuto grezzo di un foglio Excel/CSV destrutturato (colonne in
ordine qualsiasi, intestazioni assenti o in italiano discorsivo, dati sporchi).

Estrai UNA voce per ogni condòmino/unità abitativa. Per ognuno individua:
- nome: nome di battesimo (stringa, obbligatorio; se trovi solo "Cognome Nome"
  separa al meglio)
- cognome: cognome
- unita: identificativo dell'unità (es. "Scala A - Interno 3", "Int. 5",
  "Box 2"); stringa libera
- millesimi: quota millesimale come numero (es. 45.5); null se assente
- telefono: numero di telefono in formato compatto; null se assente
- email: indirizzo email; null se assente

Regole:
- Ignora righe di intestazione, totali, note e righe vuote.
- Non inventare dati: se un campo non c'è, usa null.
- I millesimi devono essere un numero (usa il punto come separatore decimale).

Rispondi SOLO con un oggetto JSON:
{"inquilini": [{"nome": "...", "cognome": "...", "unita": "...",
"millesimi": 0, "telefono": "...", "email": "..."}]}"""


def _file_to_text(filename: str, content: bytes) -> str:
    """Trasforma il file caricato in testo tabellare leggibile.

    Per i CSV/TXT passiamo il testo grezzo così com'è: file destrutturati hanno
    righe con numero di colonne variabile, che farebbero fallire un parser a
    colonne fisse. È l'LLM a dare struttura, quindi il testo grezzo va benissimo.
    """
    name = (filename or "").lower()

    if name.endswith((".csv", ".txt", ".tsv")) or not name.endswith((".xlsx", ".xls")):
        # Decodifica robusta: prova UTF-8, poi fallback Latin-1 (mai un crash).
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                testo = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            testo = content.decode("utf-8", errors="replace")
        return testo.strip()[:MAX_CHARS]

    # Excel: leggi tutti i fogli, senza assumere intestazioni.
    import pandas as pd

    parti = []
    fogli = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=str)
    for nome_foglio, df in fogli.items():
        df = df.fillna("")
        parti.append(f"### Foglio: {nome_foglio}\n{df.to_csv(index=False, header=False)}")

    return "\n\n".join(parti).strip()[:MAX_CHARS]


def estrai_inquilini(filename: str, content: bytes) -> list[dict]:
    """Legge il file e ritorna la lista di inquilini riconosciuti dall'LLM.

    Solleva ValueError con un messaggio leggibile in caso di problemi (file
    illeggibile, chiave mancante, risposta non valida).
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY non configurata: import automatico non disponibile.")

    try:
        testo = _file_to_text(filename, content)
    except Exception as e:
        logger.warning("Lettura file import fallita: %s", e)
        raise ValueError(f"Impossibile leggere il file: {e}")

    if not testo:
        raise ValueError("Il file risulta vuoto.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": testo},
            ],
            response_format={"type": "json_object"},
            reasoning_effort="low",
            max_completion_tokens=4096,
        )
    except Exception as e:
        logger.error("Chiamata import LLM fallita: %s", e)
        raise ValueError(f"Errore nell'analisi del file: {e}")

    raw = (response.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Risposta del modello non interpretabile.")

    inquilini = data.get("inquilini", []) if isinstance(data, dict) else []
    puliti = []
    for r in inquilini:
        if not isinstance(r, dict):
            continue
        nome = (r.get("nome") or "").strip()
        cognome = (r.get("cognome") or "").strip()
        if not nome and not cognome:
            continue
        millesimi = r.get("millesimi")
        try:
            millesimi = float(millesimi) if millesimi not in (None, "") else None
        except (TypeError, ValueError):
            millesimi = None
        puliti.append({
            "nome": nome or cognome,
            "cognome": cognome,
            "unita": (r.get("unita") or "").strip() or None,
            "millesimi": millesimi,
            "telefono": (r.get("telefono") or "").strip() or None,
            "email": (r.get("email") or "").strip() or None,
        })

    return puliti
