"""Comments inside a `$$` body, which the plain-clause renderer used to mangle.

`_upper_kw` whitespace-tokenizes whatever it is given, and a body clause kept
its same-line trailing comment -- so `-- log it for the user` shipped as
`-- LOG it FOR the user;`, keyword-cased with a stray semicolon inside the
comment, no decline, exit 0. Under `keyword_case = "lower"` it was worse: the
code lowercased while the comment kept the uppercase mangling.

The contract here is the SQL comment engine's: reproduce faithfully or decline.
A same-line trailing comment is preserved verbatim; a comment the clause
splitter merges with following code has no faithful single-line rendering and
declines as `comment inside a procedural clause`.
"""
import pytest

from sqlalign.formatter import format_sql
from sqlalign.style import Style


def _fn(body: str) -> str:
    return (f"create function f() returns int as $$\nbegin\n{body}\n"
            "  return 1;\nend;\n$$ language plpgsql;")


def test_a_trailing_comment_survives_verbatim():
    result = format_sql(_fn("  raise notice 'hi'; -- log it for the user"), "postgres")
    assert not result.declines, result.declines
    assert "-- log it for the user\n" in result.text + "\n"
    assert "-- LOG" not in result.text
    assert "user;" not in result.text, "the terminator moved inside the comment"


def test_keyword_case_never_reaches_a_comment():
    result = format_sql(_fn("  raise notice 'hi'; -- Log It For The User"),
                        "postgres", Style(keyword_case="lower"))
    assert not result.declines
    assert "-- Log It For The User" in result.text


def test_a_comment_that_merges_with_code_declines():
    """An own-line comment rides into the next clause, where rendering it on one
    line would swallow the code into the comment. Declining is the contract;
    mangling was the bug."""
    result = format_sql(_fn("  -- filter"), "postgres")
    assert [d.kind for d in result.declines] == ["unsupported"]
    assert "comment inside a procedural clause" in result.declines[0].reason
    assert result.text == _fn("  -- filter"), "not passed through byte-identical"


def test_a_double_dash_inside_a_string_is_not_a_comment():
    result = format_sql(_fn("  raise notice 'a -- b';"), "postgres")
    assert not result.declines
    assert "'a -- b'" in result.text


def test_it_survives_a_second_pass():
    once = format_sql(_fn("  raise notice 'hi'; -- keep"), "postgres").text
    assert format_sql(once, "postgres").text == once


@pytest.mark.parametrize("clause", [
    "  raise notice 'x'; /* keep me */",
    "  perform 1; /* mid */ perform 2;",
])
def test_block_comments_never_ship_mangled(clause):
    """Either faithfully rendered or declined -- what is forbidden is output
    that changed the comment with nothing saying so."""
    source = _fn(clause)
    result = format_sql(source, "postgres")
    if result.declines:
        assert result.text == source
    else:
        assert "/* keep me */" in result.text or "/* mid */" in result.text
