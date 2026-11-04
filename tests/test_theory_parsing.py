"""Tests for the canonical theory-header parser (header-imports issue):
comment-stripped, header-anchored — and for its two delegating consumers
(mcp_servers.lsp.pool.header_imports, build_verify.extract_imports).
No Isabelle backend needed.
"""
from __future__ import annotations

from server.app.services.build_verify import BuildVerifier
from server.app.services.theory_parsing import (
    parse_theory_header,
    strip_theory_comments,
    suggested_field,
)

TASK_TEXT = (
    '(* TASK: theorem=putnam_2023_b5 imports=Complex_Main,"HOL-Analysis.Derivative" field=HOL *)\n'
    "theory Problem\n"
    'imports Complex_Main "HOL-Analysis.Derivative"\n'
    "begin\n\n"
    'lemma putnam_2023_b5: "True"\n  sorry\n\nend\n'
)


# ------------------------------------------------------------- strip comments


def test_strip_comments_plain_and_nested():
    assert strip_theory_comments("a (* one *) b") == "a  b"
    assert strip_theory_comments("a (* outer (* inner *) still *) b") == "a  b"
    # unterminated comment swallows to EOF; newlines inside comments survive
    assert strip_theory_comments("a (* x\ny *) b") == "a \n b"


def test_strip_comments_unterminated():
    assert strip_theory_comments("a (* never closed\nb") == "a \n"


# ------------------------------------------------------------- parse header


def test_parse_header_task_comment_is_ignored():
    name, imports = parse_theory_header(TASK_TEXT)
    assert name == "Problem"
    assert imports == ["Complex_Main", "HOL-Analysis.Derivative"]
    # not a single comment word leaks through
    assert not {"field", "HOL", "theory", "Problem", "theorem"} & set(imports)


def test_parse_header_one_line_and_multiline():
    name, imports = parse_theory_header(
        'theory T imports Main "HOL-Library.Multiset" begin\nend\n'
    )
    assert name == "T"
    assert imports == ["Main", "HOL-Library.Multiset"]
    name2, imports2 = parse_theory_header(
        "theory U\n  imports\n    Main\n    Sub/Dir\nbegin\nend\n"
    )
    assert name2 == "U"
    assert imports2 == ["Main", "Sub/Dir"]


def test_parse_header_comment_inside_header():
    text = "theory V\nimports (* sneaky: imports=Fake *) Main\nbegin\nend\n"
    name, imports = parse_theory_header(text)
    assert name == "V"
    assert imports == ["Main"]


def test_parse_header_no_header_and_no_imports():
    assert parse_theory_header("lemma a: True by simp") == (None, [])
    assert parse_theory_header("theory W begin\nend\n") == ("W", [])


def test_parse_header_crlf():
    text = "(* TASK: theorem=x imports=Main field=HOL *)\r\ntheory C\r\nimports Main\r\nbegin\r\n"
    name, imports = parse_theory_header(text)
    assert name == "C"
    assert imports == ["Main"]


# ------------------------------------------------------------- suggested field


def test_suggested_field():
    assert suggested_field(["Complex_Main", "HOL-Analysis.Derivative"]) == "HOL-Analysis"
    assert suggested_field(["Main"]) is None
    assert suggested_field([]) is None
    # the largest distribution image named wins, whatever the header's order
    assert suggested_field(["HOL-Library.Multiset", "HOL-Analysis.Derivative"]) == "HOL-Analysis"
    assert suggested_field(["HOL-Algebra.Group", "HOL-Probability.Probability"]) == "HOL-Probability"
    assert (
        suggested_field(["HOL-Number_Theory.Residues", "HOL-Computational_Algebra.Primes"])
        == "HOL-Number_Theory"
    )
    # a listed image beats one outside the list; otherwise the first dotted import
    assert suggested_field(["HOL-Cardinals.Cardinals", "HOL-Library.Multiset"]) == "HOL-Library"
    assert suggested_field(["HOL-ex.Cartouche_Examples", "HOL-Cardinals.Cardinals"]) == "HOL-ex"
    assert suggested_field(["HOL.Main"]) == "HOL"
    # a path import is not a session name
    assert suggested_field(["../Common/Base"]) is None
    assert suggested_field(["Lib.v2/Foo", "../v1.2/Base"]) is None
    assert suggested_field(["../Common/Base", "HOL-Library.Multiset"]) == "HOL-Library"


# ------------------------------------------------------------- the consumers


def test_mcp_header_imports_delegates():
    """The LSP MCP delegates to the server parser THROUGH the HTTP endpoint
    (client.parse_theory_header), never by importing server code — the
    MCP/client must stay independent of the server (tests/test_dependency_rules.py).
    A fake client that answers with the server's own parser stands in for the endpoint."""
    import asyncio

    from mcp_servers.lsp.pool import header_imports

    class _FakeClient:
        async def parse_theory_header(self, text):
            name, imports = parse_theory_header(text)
            return {"theory_name": name, "imports": imports, "suggested_field": None}

    fake = _FakeClient()
    assert asyncio.run(header_imports(fake, TASK_TEXT)) == ["Complex_Main", "HOL-Analysis.Derivative"]
    assert asyncio.run(header_imports(fake, "theory T imports Main begin\nlemma a: True by simp")) == ["Main"]
    assert asyncio.run(header_imports(fake, "lemma a: True by simp")) == []


def test_build_verify_extract_imports_delegates():
    verifier = BuildVerifier.__new__(BuildVerifier)
    assert verifier.extract_imports(TASK_TEXT) == [
        "Complex_Main",
        "HOL-Analysis.Derivative",
    ]
    # default preserved: no imports found -> ["Main"]
    assert verifier.extract_imports("lemma a: True by simp") == ["Main"]
