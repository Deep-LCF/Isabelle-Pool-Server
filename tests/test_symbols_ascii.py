"""The server's symbol table is the one source of Isabelle notation (owner
rule, 2026-11-11): fill methods, try0 commands and goals come back in \\<name>
notation when ASCII_OUTPUT is on, and GET /api/v1/symbols serves the table so
clients compare text the way Isabelle reads it instead of copying a table.
No Isabelle: the table is stubbed."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.core.config import Server
from server.app.main import app
from server.app.services import fill_sorries as fs
from server.app.services import fill_stages as st
from server.app.services import unicode_normaliser as un

TABLE = {"≤": "\\<le>", "‹": "\\<open>", "›": "\\<close>", "⟹": "\\<Longrightarrow>"}


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setattr(un, "_load_symbol_table", lambda: dict(TABLE))
    monkeypatch.setattr(Server, "ASCII_OUTPUT", True)


def test_ascii_output_is_gated_and_falls_back(monkeypatch, table):
    assert un.ascii_output("using N_def ‹x ≤ a n› by (metis LeastI_ex)") == \
        "using N_def \\<open>x \\<le> a n\\<close> by (metis LeastI_ex)"
    assert un.ascii_output(None) is None
    monkeypatch.setattr(Server, "ASCII_OUTPUT", False)
    assert un.ascii_output("x ≤ y") == "x ≤ y"
    monkeypatch.setattr(Server, "ASCII_OUTPUT", True)

    def _missing():
        raise FileNotFoundError("no symbols")
    monkeypatch.setattr(un, "_load_symbol_table", _missing)
    assert un.ascii_output("x ≤ y") == "x ≤ y"          # raw, never an error


def test_fill_method_try0_and_goal_are_ascii(table):
    step = fs._classify_step({"results": ["Try this: using N_def ‹x ≤ a n› by (metis LeastI_ex) (34 ms)"]})
    assert step.outcome == "proved" and "≤" not in step.method and "\\<le>" in step.method
    r = fs.TargetResult(target=fs.SorryTarget(index=3, host_index=2, line=2, node_line=2, col=3, end_col=8, host_kind="have"))
    assert st.apply_try0(r, {"try0": "by (simp add: ‹x ≤ y›)"}) is True
    assert r.replacement == "by (simp add: \\<open>x \\<le> y\\<close>)"
    assert fs.replacement_for(1, [step.method]) == step.method


def test_symbols_endpoint_serves_the_table(table):
    out = TestClient(app).get("/api/v1/symbols").json()
    assert out["available"] is True and out["ascii_output"] is True
    assert out["symbols"]["\\<le>"] == "≤" and out["count"] == len(TABLE)


def test_symbols_endpoint_without_isabelle(monkeypatch):
    def _missing():
        raise FileNotFoundError("no symbols")
    monkeypatch.setattr(un, "_load_symbol_table", _missing)
    out = TestClient(app).get("/api/v1/symbols").json()
    assert out == {"symbols": {}, "count": 0, "available": False, "ascii_output": bool(Server.ASCII_OUTPUT)}
