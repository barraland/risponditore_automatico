"""API per la pagina 'MCP server' della dashboard: elenca i tool esposti all'assistente (nome,
parametri, descrizione) e permette di editarne la DESCRIZIONE. L'override è globale e vale su tutti
i canali (voce, WhatsApp, ElevenLabs). Auth: stesso bearer Supabase degli altri endpoint."""

import logging

from fastapi import APIRouter, Body, Header

from database import SessionLocal, ToolDescrizione
from services import tool_meta
from routers.api_documenti import _verify_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tools")


@router.get("")
async def lista_tools(authorization: str | None = Header(None)):
    """Inventario dei tool: nome, parametri (schema), descrizione di default (docstring) e override."""
    await _verify_user(authorization)
    from routers import mcp_server
    try:
        tools = mcp_server._inventario_tools()
    except Exception as e:
        logger.warning("Inventario tool non disponibile: %s", e)
        return {"tools": [], "errore": "Inventario tool non disponibile."}
    ov = tool_meta.overrides()
    out = []
    for t in tools:
        nome = getattr(t, "name", "")
        if not nome:
            continue
        out.append({
            "nome": nome,
            "parametri": getattr(t, "parameters", {}) or {},
            "default": tool_meta.docstring(nome),
            "override": ov.get(nome, ""),
        })
    out.sort(key=lambda x: x["nome"])
    return {"tools": out}


@router.post("/salva")
async def salva_tool(payload: dict = Body(...), authorization: str | None = Header(None)):
    """Salva (o azzera, se descrizione vuota) l'override della descrizione di un tool."""
    await _verify_user(authorization)
    nome = (payload.get("tool_name") or "").strip()
    desc = (payload.get("descrizione") or "").strip()
    if not nome:
        return {"ok": False, "errore": "tool_name mancante"}
    db = SessionLocal()
    try:
        row = db.query(ToolDescrizione).filter(ToolDescrizione.tool_name == nome).first()
        if not desc:                       # descrizione vuota = ripristina la docstring di default
            if row:
                db.delete(row)
                db.commit()
        elif row:
            row.descrizione = desc
            db.commit()
        else:
            db.add(ToolDescrizione(tool_name=nome, descrizione=desc))
            db.commit()
    finally:
        db.close()
    tool_meta._cache["t"] = 0.0            # invalida la cache → l'edit ha effetto quasi subito
    return {"ok": True, "reset": not desc}
