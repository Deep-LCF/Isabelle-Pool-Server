"""Isabelle's symbol table, served so clients compare text the way Isabelle
reads it (one table, the installed version's own; no client copies)."""
from fastapi import APIRouter

from server.app.core.config import Server
from server.app.services.unicode_normaliser import symbol_table

router = APIRouter(tags=["symbols"])


@router.get("/api/v1/symbols")
async def symbols():
    """``{symbols: {"\\<le>": "≤", ...}, count, ascii_output}``: the mapping
    from ``$ISABELLE_HOME/etc/symbols`` of the running server (empty, with
    ``available: false``, when the server runs without an Isabelle install).
    ``ascii_output`` says whether rendered output and fill methods come back in
    ``\\<name>`` notation."""
    try:
        table = symbol_table()
        available = True
    except FileNotFoundError:
        table, available = {}, False
    return {"symbols": table, "count": len(table), "available": available,
            "ascii_output": bool(Server.ASCII_OUTPUT)}
