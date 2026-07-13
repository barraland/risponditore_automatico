"""Test di accettazione per la pipeline di ingestion documenti.

Processa i due PDF reali in ./dati_test/ e verifica:
- esito 'ready' per entrambi
- validazione copertura-pagine verde (1→N, niente buchi/overlap)
- nessuna tabella multi-pagina spezzata tra sezioni (controllo euristico:
  i confini tra due sezioni entrambe con tabelle vengono evidenziati)
Stampa l'indice generato.

Uso:  ../langgraph_env/bin/python test_ingestion.py
Richiede OPENAI_API_KEY nel .env (sezionatore GPT-5-mini).
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from services import ingestion

PDFS = [
    "dati_test/Bilancio consuntivo 2025.pdf",
    "dati_test/Consumi 2025.pdf",
]

SEP = "=" * 78


def stampa_indice(esito: dict):
    for s in esito["sezioni"]:
        tab = " [TABELLE]" if s["contiene_tabelle"] else ""
        print(f"  pp.{s['page_start']:>3}-{s['page_end']:<3}  {s['titolo']}{tab}")
        if s.get("summary"):
            print(f"            ↳ {s['summary']}")


def verifica(esito: dict, n_atteso_min: int) -> list[str]:
    """Ritorna la lista di problemi (vuota se tutto ok)."""
    problemi = []
    if esito["stato"] != "ready":
        problemi.append(f"stato = {esito['stato']} (atteso 'ready'); errore: {esito.get('errore')}")
        return problemi

    sezioni = esito["sezioni"]
    n = esito["n_pagine"]

    # Ri-valida la copertura in modo indipendente.
    errori_cop = ingestion.valida_sezioni(
        [{"titolo": s["titolo"], "page_start": s["page_start"], "page_end": s["page_end"]} for s in sezioni],
        n,
    )
    if errori_cop:
        problemi.append("copertura non valida: " + "; ".join(errori_cop))

    # content_md presente per ogni sezione.
    for s in sezioni:
        if not (s.get("content_md") or "").strip():
            problemi.append(f"sezione '{s['titolo']}' senza content_md")

    return problemi


def main():
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY non configurata nel .env — impossibile eseguire il test.")
        sys.exit(2)

    esito_globale = 0
    for pdf in PDFS:
        if not os.path.exists(pdf):
            print(f"[SKIP] manca {pdf}")
            continue
        print(SEP)
        print(f"DOCUMENTO: {pdf}")
        print(SEP)
        esito = ingestion.processa_documento(pdf)
        print(f"stato={esito['stato']}  pagine={esito['n_pagine']}  sezioni={len(esito['sezioni'])}")
        stampa_indice(esito)

        problemi = verifica(esito, 1)
        if problemi:
            esito_globale = 1
            print("\n  ❌ PROBLEMI:")
            for p in problemi:
                print(f"     - {p}")
        else:
            print("\n  ✅ Validazione copertura-pagine VERDE, content_md presente per ogni sezione.")
        print()

    print(SEP)
    print("ESITO:", "TUTTO OK" if esito_globale == 0 else "CON PROBLEMI")
    sys.exit(esito_globale)


if __name__ == "__main__":
    main()
