"""Sledgehammer wall budgets and the 504 for a backend timeout (2026-11-09).

Plain-arm P2 run: three tip sledgehammers (timeout_s=120, 120, 180) each hit
the fixed 30 s grace on both the Scala and the Python side and came back as
HTTP 500 "Internal server error"; the client retried with a longer ATP budget
and lost 8.5 minutes. Now: grace = max(30 s, timeout_s / 2) on both sides, the
Python wait outlasts the Scala budget, and a backend timeout is a 504 whose
message names the budget.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from types import SimpleNamespace

import pytest

from server.app.errors import BackendTimeout, SessionError
from server.app.main import handle_backend_timeout
from server.app.services import sledgehammer_budget as sb
from server.app.services.session import _Isabelle_Session as Session


def test_grace_is_half_the_atp_budget_with_a_30s_floor():
    assert sb.grace_s(1) == 30 and sb.grace_s(30) == 30 and sb.grace_s(60) == 30
    assert sb.grace_s(120) == 60 and sb.grace_s(180) == 90 and sb.grace_s(300) == 150
    # the Python waits sit above the Scala budget (timeout + grace)
    for t in (30, 120, 300):
        assert sb.http_wait_tip_s(t) > t + sb.grace_s(t)
        assert sb.http_wait_at_s(t) > sb.http_wait_tip_s(t)


def _fake_session(capture: dict):
    """Enough of a Session for sledgehammer{,_at}: the backend call is captured
    instead of run, so the wait budget handed to it can be checked."""
    def _call_backend(fn, timeout=None):
        capture["timeout"] = timeout
        return capture.get("reply")
    return SimpleNamespace(
        update_activity=lambda: None, _acquire_request=lambda: None, _release_request=lambda: None,
        _call_backend=_call_backend, backend=SimpleNamespace(raw=None),
    )


def test_sledgehammer_waits_outlast_the_scala_budget():
    cap = {"reply": []}
    Session.sledgehammer(_fake_session(cap), timeout_s=120)
    assert cap["timeout"] == 120 + 60 + sb.PY_MARGIN_TIP_S           # 195, not 150
    cap = {"reply": '{"found": false}'}
    Session.sledgehammer_at(_fake_session(cap), 7, 1, timeout_s=180)
    assert cap["timeout"] == 180 + 90 + sb.PY_MARGIN_AT_S            # 295, not 220
    cap = {"reply": []}
    Session.sledgehammer(_fake_session(cap), timeout_s=30)
    assert cap["timeout"] == 30 + 30 + sb.PY_MARGIN_TIP_S            # the floor: 75


def test_call_backend_timeout_is_a_session_error_and_a_timeout_error():
    fut = concurrent.futures.Future()                                 # never resolved
    fake = SimpleNamespace(backend=SimpleNamespace(submit=lambda fn: fut))
    with pytest.raises(BackendTimeout) as ei:
        Session._call_backend(fake, lambda: None, timeout=0.01)
    assert isinstance(ei.value, SessionError) and isinstance(ei.value, TimeoutError)
    assert "0.0s" in str(ei.value) and "timed out" in str(ei.value)


def test_backend_timeout_maps_to_504_with_the_message():
    req = SimpleNamespace(url=SimpleNamespace(path="/api/v1/sessions/x/sledgehammer"))
    resp = asyncio.run(handle_backend_timeout(req, BackendTimeout("Backend call timed out after 195.0s")))
    assert resp.status_code == 504
    assert b"195.0s" in resp.body and b"Internal server error" not in resp.body
