"""Session create/acquire requests with `session_dirs`: session ROOT directories (like
`isabelle build -d`) whose sessions the theories may name, e.g. a project's own sessions
imported qualified. The session still starts on `field`, which must be a built image; the
project's theories load from source. Each directory is checked like `project`
(validate_project_path: absolute, under the allowed roots); the route checks that it holds a
ROOT or ROOTS file."""
from typing import List

from pydantic import Field, field_validator

from server.app.core.input_guards import validate_project_path

from .API_models import SessionAcquireRequest, SessionCreateRequest

_DOC = "Session ROOT directories (absolute, under the allowed roots) the theories may name."


def _check(v):
    if v is None:
        return None
    if len(v) > 64:
        raise ValueError("session_dirs: at most 64 directories")
    return [validate_project_path(d) for d in v]


class SessionCreateWithDirsRequest(SessionCreateRequest):
    session_dirs: List[str] | None = Field(default=None, description=_DOC)
    check_session_dirs = field_validator("session_dirs")(classmethod(lambda cls, v: _check(v)))


class SessionAcquireWithDirsRequest(SessionAcquireRequest):
    session_dirs: List[str] | None = Field(default=None, description=_DOC)
    check_session_dirs = field_validator("session_dirs")(classmethod(lambda cls, v: _check(v)))
