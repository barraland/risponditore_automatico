"""Tracciamento prestazioni per il RAG (e non solo).

Ogni richiesta ottiene un `Trace` con un id breve (l'HEADER comune a tutte le sue righe di log,
così distingui una richiesta dall'altra) e ad ogni tappa stampa: timestamp, tempo totale dall'inizio
e Δ dalla riga precedente. Il tracer "attivo" vive in un ContextVar, così qualunque funzione nella
stessa catena di chiamate (anche in un altro modulo, es. vettore) può fare `perf.mark(...)` senza
doverselo passare come parametro.

Uso:
    from services import perf
    perf.start("cerca q=...")     # inizio richiesta
    ...
    perf.mark("router LLM fatto")  # tappa
"""

import time
import uuid
import logging
from contextvars import ContextVar
from datetime import datetime

logger = logging.getLogger("rag.perf")

_current: ContextVar = ContextVar("rag_trace", default=None)


class Trace:
    def __init__(self, etichetta: str = ""):
        self.rid = uuid.uuid4().hex[:8]     # HEADER: identifica la richiesta
        self.etichetta = etichetta
        now = time.perf_counter()
        self.t0 = now
        self.last = now

    def mark(self, msg: str) -> None:
        now = time.perf_counter()
        tot = (now - self.t0) * 1000.0
        delta = (now - self.last) * 1000.0
        self.last = now
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        logger.info("[rag %s] %s | tot %8.1fms | Δ %8.1fms | %s", self.rid, ts, tot, delta, msg)


def start(etichetta: str = "") -> Trace:
    """Apre una nuova traccia e la rende quella attiva per il contesto corrente."""
    t = Trace(etichetta)
    _current.set(t)
    t.mark(f"⏱️  START — {etichetta}" if etichetta else "⏱️  START")
    return t


def mark(msg: str) -> None:
    """Registra una tappa sulla traccia attiva (no-op se non ce n'è una)."""
    t = _current.get()
    if t is not None:
        t.mark(msg)


def rid() -> str:
    t = _current.get()
    return t.rid if t is not None else "--------"
