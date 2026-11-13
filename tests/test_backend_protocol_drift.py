"""The Python ``ReplBackend`` Protocol must mirror the Scala ``ReplBackend``
(docs/ISSUES.md REPL-12, owner decision D3 of 2026-11-13).

Py4J dispatches by name at run time, so a Scala method missing from the
Protocol, or a Protocol entry whose signature drifted, fails nowhere until a
call goes wrong in production. mypy can only check the call sites against the
Protocol; this test checks the Protocol against Scala. No Isabelle needed: it
reads the sources.

Compared, in both directions: the public method names of ``class ReplBackend``
and its four ``Backend_*`` traits; per method the parameter count, each
parameter type and the return type, through ``_BASE`` / ``_GENERIC`` below. A
Scala type with no mapping is reported by name, so the table grows on purpose.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SCALA_DIR = ROOT / "server" / "repl" / "src" / "main" / "scala" / "repl"
PROTOCOL_FILE = ROOT / "server" / "repl" / "src" / "python" / "repl_backend_gateway.py"

# (file, kind, name): the class and the traits that make up its public surface
SCALA_SOURCES = [
    ("repl_backend.scala", "class", "ReplBackend"),
    ("backend_lifecycle.scala", "trait", "Backend_Lifecycle"),
    ("backend_probes.scala", "trait", "Backend_Probes"),
    ("backend_chunk_ops.scala", "trait", "Backend_Chunk_Ops"),
    ("backend_file_ops.scala", "trait", "Backend_File_Ops"),
]
# public but not a wire method: the generic result wrapper of the class
EXCLUDED = {"build_result"}

_BASE = {
    "Int": "int",
    "Long": "int",
    "String": "str",
    "Boolean": "bool",
    "Unit": "None",
    "Repl_Result": "ReplResult",
    "EnvStateID": "EnvStateID",
}
_GENERIC = {"java.util.List": "JavaList", "java.util.Map": "JavaMap"}


class Sig(NamedTuple):
    """One method signature: parameter types and return type, as written."""

    params: List[str]  # one type per parameter, as written
    ret: Optional[str]  # None: no explicit return type (Scala) / annotation (Python)


class UnmappedType(Exception):
    """A Scala type the mapping table does not cover."""


# --------------------------------------------------------------------- Scala


def _blank(ch: str) -> str:
    return "\n" if ch == "\n" else " "


def strip_scala(src: str) -> str:  # pylint: disable=too-many-statements
    """``src`` with comments and string-literal contents blanked (newlines
    kept, one output character per input character), so a brace, a ``def`` or a
    ``//`` inside them cannot be read as code. Handles nested block comments,
    triple-quoted strings, escapes, char literals and ``${...}`` interpolation
    (whose code may itself hold strings)."""
    out: List[str] = []
    n = len(src)

    def block_comment(i: int) -> int:
        depth = 0
        j = i
        while j < n:
            if src.startswith("/*", j):
                depth, j = depth + 1, j + 2
            elif src.startswith("*/", j):
                depth, j = depth - 1, j + 2
                if depth == 0:
                    return j
            else:
                j += 1
        raise ValueError("unterminated block comment")

    def string(i: int, interpolated: bool) -> int:
        delim = '"""' if src.startswith('"""', i) else '"'
        out.extend(delim)
        i += len(delim)
        while i < n:
            if delim == '"' and src[i] == "\\":
                out.extend("  ")
                i += 2
            elif src.startswith(delim, i):
                out.extend(delim)
                return i + len(delim)
            elif interpolated and src.startswith("${", i):
                mark = len(out)
                out.extend("${")
                i = code(i + 2, inside_braces=True)
                out.append("}")
                i += 1
                out[mark:] = [_blank(c) for c in out[mark:]]
            else:
                out.append(_blank(src[i]))
                i += 1
        raise ValueError("unterminated string literal")

    def code(i: int, inside_braces: bool) -> int:
        depth = 0
        while i < n:
            c = src[i]
            if src.startswith("//", i):
                j = src.find("\n", i)
                j = n if j < 0 else j
                out.extend(" " * (j - i))
                i = j
            elif src.startswith("/*", i):
                j = block_comment(i)
                out.extend(_blank(ch) for ch in src[i:j])
                i = j
            elif c == '"':
                prev = src[i - 1] if i > 0 else " "
                i = string(i, interpolated=prev.isalnum() or prev == "_")
            elif c == "'" and i + 2 < n and (src[i + 1] == "\\" or src[i + 2] == "'"):
                j = src.index("'", i + 2) + 1  # char literal, e.g. '=' or '\n'
                out.extend("'" + " " * (j - i - 2) + "'")
                i = j
            else:
                if inside_braces:
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        if depth == 0:
                            return i  # the caller emits the closing brace
                        depth -= 1
                out.append(c)
                i += 1
        if inside_braces:
            raise ValueError("unterminated ${...} interpolation")
        return i

    code(0, inside_braces=False)
    result = "".join(out)
    assert len(result) == n
    return result


def _matching(s: str, i: int, open_: str, close: str) -> int:
    """Index just after the bracket that closes ``s[i] == open_``."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == open_:
            depth += 1
        elif s[j] == close:
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError(f"unbalanced {open_!r}")


def _split_top(text: str) -> List[str]:
    """Split on commas outside brackets/parentheses."""
    parts, depth, cur = [], 0, []
    for c in text:
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _param_type(param: str) -> str:
    """``name: Type = default`` -> ``Type`` (a by-name ``=> A`` stays whole)."""
    _, _, rest = param.partition(":")
    m = re.search(r"=(?!>)", rest)
    return " ".join((rest[: m.start()] if m else rest).split())


def _parse_sig(s: str, i: int) -> Sig:
    """The signature that follows a member name at ``s[i]``."""
    i = len(s) - len(s[i:].lstrip())
    if i < len(s) and s[i] == "[":  # type parameters
        i = _matching(s, i, "[", "]")
    params: List[str] = []
    while True:
        i = len(s) - len(s[i:].lstrip())
        if i < len(s) and s[i] == "(":
            j = _matching(s, i, "(", ")")
            params += [_param_type(p) for p in _split_top(s[i + 1 : j - 1])]
            i = j
        else:
            break
    if i >= len(s) or s[i] != ":":
        return Sig(params, None)
    j, depth = i + 1, 0
    while j < len(s):
        c = s[j]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0 and (c == "{" or (c == "=" and s[j + 1 : j + 2] != ">")):
            break
        j += 1
    return Sig(params, " ".join(s[i + 1 : j].split()))


_DEF = re.compile(r"def\s+([A-Za-z_][A-Za-z0-9_]*)")


def _body_start(s: str, header_end: int) -> int:
    """Index of the ``{`` that opens a class/trait body (skips the constructor)."""
    depth = 0
    for i in range(header_end, len(s)):
        c = s[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "{" and depth == 0:
            return i
    raise ValueError("no body")


def scala_members(src: str, kind: str, name: str) -> List[Tuple[str, Sig]]:
    """Public ``def``s declared directly in the body of ``kind name``: not
    private/protected, not local defs inside a method, not members of a
    companion object of the same name."""
    s = strip_scala(src)
    m = re.search(rf"\b{kind}\s+{re.escape(name)}\b", s)
    if m is None:
        raise ValueError(f"{kind} {name} not found")
    i = _body_start(s, m.end()) + 1
    depth, found = 1, []
    while depth > 0:
        if i >= len(s):
            raise ValueError(f"{kind} {name}: unbalanced braces")
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 1 and c == "d" and not (s[i - 1].isalnum() or s[i - 1] == "_"):
            dm = _DEF.match(s, i)
            if dm:
                modifiers = s[s.rfind("\n", 0, i) + 1 : i]
                if not re.search(r"\b(private|protected)\b", modifiers):
                    found.append((dm.group(1), _parse_sig(s, dm.end())))
                i = dm.end()
                continue
        i += 1
    return found


def class_mixins(src: str, name: str) -> List[str]:
    """The ``Backend_*`` traits named in ``class name ... extends ... {``."""
    s = strip_scala(src)
    m = re.search(rf"\bclass\s+{re.escape(name)}\b", s)
    assert m is not None
    header = s[m.end() : _body_start(s, m.end())]
    clause = header[header.rfind(")") + 1 :]
    return re.findall(r"\b(Backend_\w+)\b", clause)


# -------------------------------------------------------------------- Python


def _canon(t: str) -> str:
    t = t.replace('"', "").replace("'", "").replace("py4j.java_collections.", "")
    return "".join(t.split())


def protocol_methods(src: str, cls: str = "ReplBackend") -> Dict[str, Sig]:
    """The methods of Protocol class ``cls``: annotation text per parameter
    (``self`` dropped) and the return annotation. Read with ``ast``, so comments
    between ``(`` and ``self`` (pylint pragmas) and multi-line signatures are
    no concern."""
    for node in ast.parse(src).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            out: Dict[str, Sig] = {}
            for f in node.body:
                if isinstance(f, ast.FunctionDef):
                    params = [
                        ast.unparse(a.annotation) if a.annotation is not None else "?"
                        for a in f.args.args[1:]
                    ]
                    ret = ast.unparse(f.returns) if f.returns is not None else None
                    out[f.name] = Sig(params, ret)
            return out
    raise ValueError(f"class {cls} not found")


# ------------------------------------------------------------------- compare


def scala_to_py(t: str) -> str:
    """The Python annotation a Scala type must have in the Protocol."""
    t = "".join(t.split())
    m = re.fullmatch(r"([\w.]+)\[(.*)\]", t)
    if m:
        head, inner = m.groups()
        if head not in _GENERIC:
            raise UnmappedType(t)
        return f"{_GENERIC[head]}[{','.join(scala_to_py(a) for a in _split_top(inner))}]"
    if t not in _BASE:
        raise UnmappedType(t)
    return _BASE[t]


def compare(scala: Dict[str, Sig], py: Dict[str, Sig]) -> List[str]:
    """Every disagreement between the Scala surface and the Protocol."""
    problems = [f"{n}: public in Scala, no Protocol entry" for n in sorted(scala.keys() - py.keys())]
    problems += [f"{n}: Protocol entry, no public Scala method" for n in sorted(py.keys() - scala.keys())]
    for name in sorted(scala.keys() & py.keys()):
        s, p = scala[name], py[name]
        if s.ret is None:
            problems.append(f"{name}: no explicit Scala return type")
            continue
        if len(s.params) != len(p.params):
            problems.append(
                f"{name}: {len(s.params)} Scala parameter(s), {len(p.params)} in the Protocol")
            continue
        try:
            want = [scala_to_py(t) for t in s.params]
            want_ret = scala_to_py(s.ret)
        except UnmappedType as exc:
            problems.append(f"{name}: no mapping for Scala type {exc}")
            continue
        for k, (w, got) in enumerate(zip(want, p.params), start=1):
            if _canon(w) != _canon(got):
                problems.append(f"{name}: parameter {k} is {got} in the Protocol, Scala says {w}")
        if _canon(want_ret) != _canon(p.ret or "?"):
            problems.append(f"{name}: returns {p.ret} in the Protocol, Scala says {want_ret}")
    return problems


def _scala_surface() -> Tuple[Dict[str, Sig], List[str]]:
    members: Dict[str, Sig] = {}
    duplicates: List[str] = []
    for file, kind, name in SCALA_SOURCES:
        for member, sig in scala_members((SCALA_DIR / file).read_text(encoding="utf-8"), kind, name):
            if member in EXCLUDED:
                continue
            if member in members:
                duplicates.append(member)
            members[member] = sig
    return members, duplicates


# --------------------------------------------------------------------- tests


def test_protocol_matches_scala() -> None:
    """The real check: the Protocol mirrors the Scala surface exactly."""
    scala, duplicates = _scala_surface()
    py = protocol_methods(PROTOCOL_FILE.read_text(encoding="utf-8"))
    assert not duplicates, f"overloaded/duplicated Scala members (Py4J picks by arity): {duplicates}"
    # guard against a parser that silently finds nothing
    assert len(scala) >= 30, sorted(scala)
    problems = compare(scala, py)
    assert not problems, "Protocol drift:\n  " + "\n  ".join(problems)


def test_class_mixes_in_exactly_the_scanned_traits() -> None:
    """A new Backend_* trait must be added to SCALA_SOURCES, or it goes unchecked."""
    src = (SCALA_DIR / "repl_backend.scala").read_text(encoding="utf-8")
    scanned = {name for _, kind, name in SCALA_SOURCES if kind == "trait"}
    assert set(class_mixins(src, "ReplBackend")) == scanned


_FIXTURE_SCALA = '''
object Probe_Things {
  def companion_only(x: Int): Int = x   // companion object: not on the backend
}

/** def in_scaladoc(x: Int): String -- comments are not code */
trait Probe_Things { this: ReplBackend =>
  def parameterless: String = "a // not a comment { not a brace"
  def multi_line(
      index: Int,   // a comment between parameters
      provers: String
  ): java.util.List[String] = {
    def local_helper(y: Int): Int = y  // local: not a member
    val s = s"budget ${index.toString + "}"} ms"
    val c = '{'
    List(s).asJava
  }
  private def hidden(x: Int): Int = x
  protected def also_hidden(): Unit = ()
  def by_name[A](body: => A, flag: Boolean = false): Repl_Result = build_result(body)
  def maps(): java.util.Map[String, Int] = new java.util.HashMap()
}
'''


def test_scala_parser_handles_the_pitfalls() -> None:
    """Comments, strings, ${...}, char literals, local and hidden defs, companions."""
    got = dict(scala_members(_FIXTURE_SCALA, "trait", "Probe_Things"))
    assert set(got) == {"parameterless", "multi_line", "by_name", "maps"}
    assert got["parameterless"] == Sig([], "String")
    assert got["multi_line"] == Sig(["Int", "String"], "java.util.List[String]")
    assert got["by_name"] == Sig(["=> A", "Boolean"], "Repl_Result")
    assert got["maps"] == Sig([], "java.util.Map[String, Int]")


def test_python_parser_reads_through_pragmas() -> None:
    """A pylint pragma between ( and self must not hide a method."""
    src = '''
class ReplBackend(Protocol):
    def a(  # pylint: disable=too-many-arguments
        self, index: int, provers: str
    ) -> py4j.java_collections.JavaList[str]: ...
'''
    assert protocol_methods(src) == {"a": Sig(["int", "str"], "py4j.java_collections.JavaList[str]")}


def test_compare_reports_a_missing_entry_and_a_wrong_count() -> None:
    """Negative check: what REPL-12 was (a missing entry), plus a wrong arity."""
    scala = {
        "a": Sig(["Int"], "String"),
        "b": Sig([], "Unit"),
        "c": Sig(["Long", "Boolean"], "java.util.Map[String, Int]"),
    }
    py = {
        "a": Sig([], "str"),  # parameter missing
        "c": Sig(["int", "bool"], "JavaMap[str, int]"),
    }  # "b" missing
    problems = compare(scala, py)
    assert problems == [
        "b: public in Scala, no Protocol entry",
        "a: 1 Scala parameter(s), 0 in the Protocol",
    ]


def test_compare_reports_wrong_types_and_unmapped_types() -> None:
    """Negative check: wrong types, an unmapped type, a missing return type."""
    problems = compare(
        {"a": Sig(["Int"], "String"), "u": Sig([], "Option[Int]"), "v": Sig([], None)},
        {"a": Sig(["str"], "bool"), "u": Sig([], "int"), "v": Sig([], "None")},
    )
    assert problems == [
        "a: parameter 1 is str in the Protocol, Scala says int",
        "a: returns bool in the Protocol, Scala says str",
        "u: no mapping for Scala type Option[Int]",
        "v: no explicit Scala return type",
    ]
