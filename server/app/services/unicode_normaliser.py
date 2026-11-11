"""Normalise Isabelle/HOL theory text: convert Unicode mathematical symbols to
their Isabelle \<name> escape notation using Isabelle's own canonical symbol table
($ISABELLE_HOME/etc/symbols).

Isabelle/jEdit transparently converts between Unicode codepoints and the
\<name> notation using the same table.  This module re-uses that table so that
theory files submitted to the bigstep endpoint (`isabelle build`) are always in
canonical Isabelle-ASCII format.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional


def _isabelle_home() -> Path:
    # Respect the ISABELLE_HOME environment variable; fall back to the path
    # used inside the Docker container.
    env = os.environ.get("ISABELLE_HOME")
    if env:
        return Path(env)
    # Fallbacks for Docker (docker-compose.yml) and common install locations.
    for candidate in ("/opt/isabelle", "/usr/local/isabelle", "/home/isabelle/isabelle"):
        p = Path(candidate)
        if (p / "etc" / "symbols").exists():
            return p
    return Path("/opt/isabelle")


# Matches lines like:  \<forall>    code: 0x002200  group: logic
# Only lines with a codepoint are included.
_UNICODE_RE = re.compile(r"^(\\<[^>]+>)\s+code:\s+(0x[0-9a-fA-F]+)", re.MULTILINE)

# Regex that matches any lone surrogate half (U+D800–U+DFFF) — these are
# invalid in well-formed UTF-8 and must be stripped.
_SURROGATE_FILTER = ord


@lru_cache(maxsize=1)
def _load_symbol_table() -> dict[str, str]:
    """Parse $ISABELLE_HOME/etc/symbols and return {chr(codepoint): \\<name>}."""
    symbols_file = _isabelle_home() / "etc" / "symbols"
    if not symbols_file.exists():
        raise FileNotFoundError(
            f"Isabelle symbol table not found at {symbols_file}. "
            f"Set ISABELLE_HOME or ensure the symbols file is installed."
        )

    mapping: dict[str, str] = {}
    text = symbols_file.read_text(encoding="utf-8")
    for m in _UNICODE_RE.finditer(text):
        name = m.group(1)
        code_str = m.group(2)
        try:
            codepoint = int(code_str, 16)
        except ValueError:
            continue
        # Only include entries where the codepoint is a valid Unicode scalar
        # (not a lone surrogate).
        if 0xD800 <= codepoint <= 0xDFFF:
            continue
        mapping[chr(codepoint)] = name
    return mapping


def symbol_table() -> dict[str, str]:
    """The installed Isabelle's table as ``{\\<name>: unicode char}`` (the
    inverse of the private map), for clients that must compare text the way
    Isabelle reads it: served by ``GET /api/v1/symbols``."""
    return {name: ch for ch, name in _load_symbol_table().items()}


def ascii_output(text: Optional[str]) -> Optional[str]:
    """Text the server WRITES INTO a document (a fill method, a try0 command,
    a goal shown beside it) in the notation the agents are told to write,
    gated by ``Server.ASCII_OUTPUT`` like every rendered output (the v1
    serializers do the same for goals and query results). Sledgehammer's
    rendered methods carry Unicode (`‹x ≤ a n›`); a client that re-types them
    as `\\<le>` and compares bytes sees two different lines (nearAI_Isabelle
    rep 1, 2026-11-11). Falls back to the raw text without a symbol table."""
    from server.app.core.config import Server  # noqa: PLC0415  (config is import-light)

    if text is None or not Server.ASCII_OUTPUT:
        return text
    try:
        return normalise_for_isabelle(str(text))
    except FileNotFoundError:
        return str(text)


def normalise_for_isabelle(text: str) -> str:
    """Replace Unicode mathematical symbols with Isabelle \<name> escapes using
    Isabelle's canonical symbol table, and strip any lone surrogate characters
    (U+D800–U+DFFF) that would cause a UTF-8 encoding error.

    Returns the text unchanged if no replacements are needed.
    """
    if not text:
        return text

    # Strip lone surrogates first — they are invalid in well-formed UTF-8
    # and would cause save failures on disk.
    text = "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))

    table = _load_symbol_table()
    if not table:
        return text

    # Build the regex once from the table keys.
    _symbol_re = re.compile("|".join(re.escape(ch) for ch in table))

    return _symbol_re.sub(lambda m: table.get(m.group(0), ""), text)