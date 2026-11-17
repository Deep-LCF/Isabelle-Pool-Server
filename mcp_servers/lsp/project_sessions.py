"""Session-qualified imports of a file's own project (`imports "Sat_Spec.Sat_Spec"`).

Such an import names a session defined by a ROOT file of the project the file belongs to. The
server can resolve it only when it knows the project's session directories (`isabelle build -d`),
and a session can only start on an image that exists (the server never builds one during session
start). So, for a file inside a git repository whose ROOT files define the sessions its imports
name, the binding and scratch sessions are acquired with

  - session_dirs: every directory of the repository that holds a ROOT file, and
  - field: the first ancestor of the imported session that the project does not define (the
    image the project builds on, e.g. AutoCorrode); the project's own theories load from source.

Files whose imports name no session of their project keep the previous behaviour (no dirs, the
field from the server's suggestion).
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

# Directories never searched for ROOT files: VCS data, build output, and the scratch copies and
# worktrees that tools create inside a repository.
_SKIP_DIRS = {"target", "dev", "worktrees", "node_modules", "__pycache__", "sessions"}
_MAX_DIRS = 64

# `session "Name" (groups) in "dir" = "Parent" +` (quotes optional); `chapter` lines are ignored.
_SESSION_RE = re.compile(
    r'\bsession\s+"?([A-Za-z0-9_+\-.]+)"?\s*(?:\([^)]*\)\s*)?(?:in\s+"?[^"=\s]+"?\s*)?=\s*"?([A-Za-z0-9_+\-.]+)"?')
_QUALIFIED_RE = re.compile(r'^"?([A-Za-z0-9_+\-]+)\.[A-Za-z0-9_\'.]+"?$')


def find_project_root(path: str) -> Optional[str]:
    """The nearest ancestor directory of `path` that is the root of a git repository (holds a
    `.git` directory or file), or None."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def project_session_dirs(root: str) -> List[str]:
    """The directories under `root` that hold a ROOT file, sorted (at most _MAX_DIRS)."""
    found: List[str] = []
    for d, subdirs, files in os.walk(root):
        subdirs[:] = sorted(s for s in subdirs if not s.startswith(".") and s not in _SKIP_DIRS)
        if "ROOT" in files:
            found.append(d)
            if len(found) >= _MAX_DIRS:
                break
    return sorted(found)


def session_parents(dirs: List[str]) -> Dict[str, str]:
    """Session name -> parent session, from the ROOT files of `dirs`."""
    parents: Dict[str, str] = {}
    for d in dirs:
        try:
            with open(os.path.join(d, "ROOT"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        for name, parent in _SESSION_RE.findall(text):
            parents.setdefault(name, parent)
    return parents


def outside_ancestor(session: str, parents: Dict[str, str]) -> Optional[str]:
    """The first ancestor of `session` (itself excluded) that `parents` does not define, or None
    (a cycle, or a session the project does not define)."""
    if session not in parents:
        return None
    seen = set()
    s = session
    while s in parents:
        if s in seen:
            return None
        seen.add(s)
        s = parents[s]
    return s


def project_context(imports: List[str], file_path: str) -> Tuple[Optional[str], Tuple[str, ...]]:
    """(field, session_dirs) for a file whose imports name sessions of its own project, else
    (None, ()). The field is the image the first such import's session builds on."""
    root = find_project_root(file_path)
    if root is None:
        return None, ()
    project_sessions: List[str] = []
    for name in imports:
        m = _QUALIFIED_RE.match(name)
        if m:
            project_sessions.append(m.group(1))
    if not project_sessions:
        return None, ()
    dirs = project_session_dirs(root)
    parents = session_parents(dirs)
    for s in project_sessions:
        field = outside_ancestor(s, parents)
        if field is not None:
            return field, tuple(dirs)
    return None, ()
