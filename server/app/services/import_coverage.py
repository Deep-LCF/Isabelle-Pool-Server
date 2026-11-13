"""Do a session's theories cover the imports a client asks ``enter_theory`` for?

IPS fixes a session's theory context when the session is acquired: a node's
context comes from the wrapper theory that imports the session's theories,
and the imports in the node's own header only gate loading (Scala
``Edit_Utils.get_thy_header_edits``). A request for imports outside that
context used to be accepted silently, and the theory was then checked
without them (training-metrics review afd3de9: a corpus replay on ``Main``
sessions; the July HOL-Analysis small-step run, 2 of 93 files). The backend
answers from Isabelle's own import graph (``Repl_Session.uncovered_imports``);
the route turns a non-empty answer into a 409.
"""

from __future__ import annotations

from typing import List, Protocol, Sequence

from server.app.core.logging import get_logger
from server.app.errors import HeaderFailed, SessionError
from server.app.services.success_checker import classify_error_kind
from server.app.services.threaded_backend import ThreadedBackend

logger = get_logger(__name__)

COVERAGE_TIMEOUT_S = 30.0
HEADER_FAILED_PREFIX = "theory header failed"
UNJUDGED_PREFIX = "?unjudged"  # the backend could not complete the ancestry


class _BackendHolder(Protocol):  # pylint: disable=too-few-public-methods
    """What ``uncovered_imports`` needs of a session: its typed backend, so the
    backend call below is checked against the ``ReplBackend`` Protocol."""

    @property
    def backend(self) -> ThreadedBackend:
        """The session's worker-thread backend."""


def uncovered_imports(session: _BackendHolder, imports: Sequence[str]) -> List[str]:
    """The requested imports outside the session's context (empty when all are
    covered, or when the backend cannot judge them: unqualified project names,
    an ancestry it cannot complete). The backend walks the session's wrapper
    theory; ``session.theories`` are its fallback roots."""
    theories = [str(t) for t in (getattr(session, "theories", None) or [])]
    wanted = [str(i) for i in imports if str(i).strip()]
    if not wanted:
        return []
    raw = session.backend.raw
    fut = session.backend.submit(
        lambda: raw.uncovered_imports("\n".join(theories), "\n".join(wanted))
    )
    out = fut.result(timeout=COVERAGE_TIMEOUT_S)
    lines = [line for line in str(out or "").split("\n") if line.strip()]
    for line in lines:
        if line.startswith(
            UNJUDGED_PREFIX
        ):  # fail-open: visible in the log (5a4ff3a S1)
            logger.warning(
                "import coverage not judged (enter_theory proceeds) session=%s "
                "theories=%s imports=%s: %s",
                getattr(session, "session_id", None),
                theories,
                wanted,
                line,
            )
    return [line for line in lines if not line.startswith(UNJUDGED_PREFIX)]


def as_header_failed(exc: SessionError) -> SessionError:
    """A header Isabelle rejected (an import it cannot load, a malformed name)
    becomes HeaderFailed (422); a timeout or a memory failure while loading, and
    every typed subclass (SessionNotFound, ...), keep their own handling."""
    msg = str(exc.error or exc)
    # exact type: a typed subclass (SessionNotFound, ...) keeps its own handler
    plain = type(exc) is SessionError  # pylint: disable=unidiomatic-typecheck
    if not plain or not msg.startswith(HEADER_FAILED_PREFIX):
        return exc
    if "timed out" in msg.lower() or classify_error_kind(msg) is not None:
        return exc
    return HeaderFailed(error=msg, execution_time=exc.execution_time)
