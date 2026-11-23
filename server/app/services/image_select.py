"""Choose the session image a session starts on, so that theories already built into an image are
not checked again from source.

A file's qualified imports (`Micro_Rust_Std_Lib.StdLib_All`) name theories; an image covers them
when they are among the theories built into it or into the images it was built on (the build
database `log/<session>.db` of each image records both: the exports of its theories and its input
heaps). The proposed field is kept when it covers the imports and is up to date; otherwise the
smallest up-to-date image that covers them is chosen; when none does, the proposed field stays.
Up to date means `isabelle build -n -b` (no build) succeeds for it, with the session directories
the session will use. That check takes seconds and is run on every call: it is not cached, because a
cache keyed on the image file misses edits to the image's source theories (the image file does not
change when they do), and then a stale image is chosen and the session start fails.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from server.app.services.heap_pool import default_isabelle as _isabelle

_QUALIFIED = re.compile(r'^"?([A-Za-z0-9_+\-]+\.[A-Za-z0-9_\'.]+)"?$')
_lock = threading.Lock()
_env: Optional[Dict[str, str]] = None
_theories: Dict[Tuple[str, float], Set[str]] = {}


def _getenv() -> Dict[str, str]:
    global _env
    if _env is None:
        out = subprocess.run(
            [_isabelle(), "getenv", "-b", "ISABELLE_HEAPS", "ISABELLE_HEAPS_SYSTEM", "ML_SYSTEM"],
            capture_output=True, text=True, timeout=60, check=True).stdout
        _env = dict(zip(("ISABELLE_HEAPS", "ISABELLE_HEAPS_SYSTEM", "ML_SYSTEM"), out.splitlines()))
    return _env


def images() -> Dict[str, Path]:
    """Session name -> image file, user images before system ones."""
    env = _getenv()
    found: Dict[str, Path] = {}
    ml = env.get("ML_SYSTEM", "")
    for base in (env.get("ISABELLE_HEAPS", ""), env.get("ISABELLE_HEAPS_SYSTEM", "")):
        # <heaps>/<ML_SYSTEM>_<platform>/<session> (ML_PLATFORM is not visible to getenv)
        dirs = sorted(Path(base).glob(f"{ml}_*")) if base and ml else []
        for f in (f for d in dirs for f in sorted(d.iterdir())):
            if f.is_file() and f.name not in found:
                found[f.name] = f
    return found


def _db_theories(image: Path) -> Tuple[Set[str], List[str]]:
    """Theories built into `image` and the names of its input heaps, from its build database."""
    db = image.parent / "log" / f"{image.name}.db"
    if not db.is_file():
        return set(), []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute("select distinct theory_name from isabelle_exports")}
        row = con.execute("select input_heaps from isabelle_session_info").fetchone()
    finally:
        con.close()
    inputs = [ln.split()[-1] for ln in (row[0] if row and row[0] else "").splitlines() if ln.split()]
    return names, inputs


def theories(name: str, imgs: Dict[str, Path]) -> Set[str]:
    """Theories available on image `name`: its own and those of the images it was built on."""
    image = imgs[name]
    key = (name, image.stat().st_mtime)
    if key not in _theories:
        own, inputs = _db_theories(image)
        for parent in inputs:
            if parent in imgs and parent != name:
                own = own | theories(parent, imgs)
        _theories[key] = own
    return _theories[key]


def current(name: str, dirs: Tuple[str, ...]) -> bool:
    """`isabelle build -n -b` succeeds for `name` (not cached; see the module docstring)."""
    cmd = [_isabelle(), "build", "-n", "-b"] + [a for d in dirs for a in ("-d", d)] + [name]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=300).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def select_field(imports: List[str], field: Optional[str], dirs: Tuple[str, ...] = ()) -> Optional[str]:
    """The image to start on for `imports` (see the module docstring)."""
    wanted = {m.group(1) for m in (_QUALIFIED.match(i) for i in imports) if m}
    if not wanted:
        return field
    with _lock:
        imgs = images()
        covering = [s for s in imgs if wanted <= theories(s, imgs)]
        if field in covering and current(field, dirs):
            return field
        for s in sorted(covering, key=lambda s: len(theories(s, imgs))):
            if s != field and current(s, dirs):
                return s
    return field
