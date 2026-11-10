"""Configuration for the Isabelle Pool Server chunk-centric MCP server (all via env, prefix ISABELLE_MCP_)."""
from __future__ import annotations

from ..common.config import env_float, env_int, env_str
from ..common.memory_advice import ToolNames

_P = "ISABELLE_MCP_"

# How THIS server names the actions the memory advice refers to (plan §2.C, D4).
TOOLS = ToolNames(
    release_idle="close_theory()",
    close="close_theory()",
    list_sessions="list_my_sessions()",
    memory_status="memory_status()",
    reopen="call enter_theory(name, imports) again",
)


class Config:
    # Where the running Isabelle Pool Server lives.
    # The Pool Server URL. `..._GYM_URL` is the pre-2026-11 name and still read.
    SERVER_URL: str = env_str(_P, "SERVER_URL", env_str(_P, "GYM_URL", "http://localhost:8000"))
    GYM_URL: str = SERVER_URL
    DEFAULT_FIELD: str = env_str(_P, "FIELD", "HOL")

    # Concurrency cap for verify_batch fan-out. Kept conservative to respect the server's
    # memory admission gate. Never exceeds the server pool size in practice.
    MAX_PARALLEL: int = env_int(_P, "MAX_PARALLEL", 4)

    # Default single wall budget (s) for verify_chunk / each verify_batch item.
    CHUNK_TIMEOUT: float = env_float(_P, "CHUNK_TIMEOUT", 180.0)
    # httpx timeout for the underlying client (must exceed CHUNK_TIMEOUT + server grace).
    HTTP_TIMEOUT: float = env_float(_P, "HTTP_TIMEOUT", 600.0)

    # A session this connection holds counts as "idle" for memory advice and
    # reminders after this many seconds without a tool call.
    ADVICE_IDLE_S: int = env_int(_P, "ADVICE_IDLE_S", 120)

    # Transport: "stdio" (local) or "streamable-http" (remote).
    TRANSPORT: str = env_str(_P, "TRANSPORT", "stdio")
    HOST: str = env_str(_P, "HOST", "127.0.0.1")
    PORT: int = env_int(_P, "PORT", 8848)
