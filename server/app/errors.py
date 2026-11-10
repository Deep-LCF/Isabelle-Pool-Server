from __future__ import annotations


class SessionError(Exception):

    def __init__(
        self,
        message: str | None = None,
        *,
        error: str | None = None,
        execution_time: float | None = None,
    ) -> None:
        detail = error or message or self.__class__.__name__
        super().__init__(detail)
        self.error = detail
        self.execution_time = execution_time


class SessionNotFound(SessionError):
    pass


class BackendTimeout(SessionError, TimeoutError):
    """A backend call did not answer within its wall budget (the Py4J future
    wait in `Session._call_backend`). Also a `TimeoutError`, so callers that
    already catch the builtin keep working. Mapped to 504 by main.py so the
    client learns *what* ran out of time instead of the generic 500 that the
    uncaught builtin produced (plain-arm P2 run, 2026-11-09: three tip
    sledgehammers at timeout_s=120/180 answered `Internal server error`)."""


class ImportsNotCovered(SessionError):
    """enter_theory asked for imports the session's context does not include:
    IPS fixes a session's theory context when it is acquired, and the imports
    of a theory header only gate loading, so the theory would be checked
    without them (docs/ISSUES.md; training-metrics review afd3de9). 409."""

    def __init__(self, uncovered: list[str], session_theories: list[str]) -> None:
        super().__init__(
            error=(
                f"imports not covered by this session's theories {session_theories}: "
                f"{uncovered}; acquire a session with these imports"
            )
        )
        self.uncovered = list(uncovered)
        self.session_theories = list(session_theories)


class HeaderFailed(SessionError):
    """enter_theory's generated header was rejected by Isabelle: an import that
    cannot be loaded, an illegal or malformed name. The client's input, not a
    server failure: 422 (review 83baa0a S1; it used to answer 500)."""


class SessionStartError(SessionError):
    pass


class GatewayUnavailable(SessionError):
    pass


class PoolExhausted(SessionError):
    """Raised when the session pool is full and all sessions are actively
    processing requests, so no eviction candidate is available."""
    pass


class MemoryPressure(PoolExhausted):
    """Raised when memory admission refuses new work (session create/acquire,
    big-step or heap build). Carries the structured facts the 503 body shows:
    numbers, state, and the sessions involved — never lease ids. The wording of
    any advice is the client's (MCP's) job (owner decision D4)."""

    def __init__(
        self,
        message: str | None = None,
        *,
        facts: dict[str, object] | None = None,
        retry_after_s: int = 5,
        **kwargs: object,
    ) -> None:
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.facts: dict[str, object] = dict(facts or {})
        self.retry_after_s = retry_after_s


class SessionLeaseError(SessionError):
    """Raised when a caller uses an invalid or missing lease token."""
    pass


class SessionBusyError(SessionError):
    """Raised when a session cannot be released/closed because work is in flight."""
    pass
