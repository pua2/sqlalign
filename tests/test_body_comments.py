"""Comments inside a `$$` body, which the plain-clause renderer used to mangle.

`_upper_kw` whitespace-tokenizes whatever it is given, and a body clause kept
its same-line trailing comment -- so `-- log it for the user` shipped as
`-- LOG it FOR the user;`, keyword-cased with a stray semicolon inside the
comment, no decline, exit 0. Under `keyword_case = "lower"` it was worse: the
code lowercased while the comment kept the uppercase mangling.

The contract here is the SQL comment engine's: reproduce faithfully or decline.
A same-line trailing comment is preserved verbatim, and an own-line comment
stays on its own line above the statement it introduces. Only a comment with
code after it ON THE SAME LINE has no faithful rendering, and that declines as
`comment inside a procedural clause`.

The own-line case declined at first, which was a worse bug than the mangling it
replaced: the clause splitter glues such a comment to the statement below it, so
one `-- note` above a `DELETE` sent the whole procedure through untouched. Every
shape below is one that a real procedure hit.
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


@pytest.mark.parametrize("body", [
    pytest.param("  -- filter", id="before-a-statement"),
    pytest.param("  perform 1;\n  -- filter", id="between-statements"),
    pytest.param("  -- one\n  -- two", id="two-in-a-row"),
    pytest.param("  -- spaced\n", id="followed-by-a-blank-line"),
])
def test_an_own_line_comment_stays_on_its_own_line(body):
    """The comment comes out once, on a line of its own, and nothing declines.

    `_fn` puts a `RETURN` after `body`, so each of these is the shape that
    broke: an own-line comment the splitter merges with the code beneath it.
    """
    result = format_sql(_fn(body), "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    for comment in [ln.strip() for ln in body.splitlines() if ln.strip().startswith("--")]:
        assert f"\n{comment}\n" in result.text, f"{comment} is no longer on its own line"
    assert "RETURN 1;" in result.text, "the statement below the comment was lost"


@pytest.mark.parametrize("body", [
    pytest.param("  -- guard\n  if 1 = 1 then raise notice 'x'; end if;", id="before-IF"),
    pytest.param("  raise notice 'x';\n  -- all done", id="before-END"),
])
def test_a_comment_does_not_hide_the_keyword_below_it(body):
    """The body parser dispatches on the first word of a clause, and the merged
    comment was that first word: an `IF` behind a comment was neither recognised
    as a block nor found as the `END`, so the procedure declined as malformed."""
    result = format_sql(_fn(body), "postgres")
    assert not result.declines, [d.reason for d in result.declines]


def test_a_comment_with_code_after_it_on_the_same_line_declines():
    """The one shape with no faithful rendering: everything after `--` belongs to
    the comment, so the code cannot follow it on that line."""
    source = _fn("  perform 1; /* mid */ perform 2;")
    result = format_sql(source, "postgres")
    assert [d.kind for d in result.declines] == ["unsupported"]
    assert "comment inside a procedural clause" in result.declines[0].reason
    assert result.text == source, "not passed through byte-identical"


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
