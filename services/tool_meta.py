"""Fonte UNICA delle descrizioni dei tool.

La descrizione canonica di ogni tool è la docstring della funzione omonima in routers/mcp_server
(quella che ElevenLabs già usa via MCP). Voce e WhatsApp NON la ridefiniscono più: la leggono da
qui. Così la descrizione vive in un solo posto — ed è il gancio per l'override editabile da
dashboard (prossimo passo: `descrizione(nome, db, azienda_id)` guarderà prima una tabella di override).
"""

import inspect


def descrizione(nome: str) -> str:
    """Descrizione canonica del tool `nome` = docstring della funzione MCP. '' se non trovata
    (in tal caso i toolset tengono la descrizione che avevano: nessun impatto sul tool-calling)."""
    try:
        from routers import mcp_server as m  # lazy: evita import circolari (mcp_server importa whatsapp_agent)
        fn = getattr(m, nome, None)
        fn = getattr(fn, "fn", fn)   # @mcp.tool() può wrappare: la funzione vera è in .fn
        doc = getattr(fn, "__doc__", None)
        return inspect.cleandoc(doc).strip() if doc else ""
    except Exception:
        return ""


def applica_realtime(tools: list) -> list:
    """Sovrascrive in-place le `description` (formato OpenAI Realtime) con quelle canoniche MCP."""
    for t in tools:
        d = descrizione(t.get("name", ""))
        if d:
            t["description"] = d
    return tools


def applica_chat(tools: list) -> list:
    """Sovrascrive in-place le `description` (formato OpenAI Chat Completions) con quelle canoniche."""
    for t in tools:
        fn = t.get("function", {})
        d = descrizione(fn.get("name", ""))
        if d:
            fn["description"] = d
    return tools
