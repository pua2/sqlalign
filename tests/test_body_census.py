"""The rewrite guard, extended to the inside of a `$$` body.

1.2's token census compared a statement's tokens either side of formatting. It
could not do that for a statement carrying a procedural body: the tokenizer sees
`$$ ... $$` as one token, so a whole-statement census compares the body as
opaque and sees nothing inside it. Descending into the token instead compares a
laid-out body against an unformatted one, which differs for reasons that are not
rewrites -- 102 tests said so when it was tried.

So `_has_body` excluded those statements outright, and a respelling inside a
procedure had nothing catching it. That is how `INTERVAL '14 days'` shipped as
`'14 DAYS'` from inside one even after the top-level case was closed.

The comparison that means something is clause against the clause it was rendered
from -- `body_clauses` already splits both sides the same way, which is what the
structural AST check relies on too. Three things had to be true before it could
run at all, and each is pinned below:

  * a `DECLARE` is a `COMMAND` token to sqlglot, which swallows the rest of the
    clause into one `STRING`. Comparing that as content made keyword casing read
    as a rewrite;
  * the formatter terminates a statement that arrived without a `;`, so counting
    terminators reported every such statement as rewritten;
  * `LANGUAGE 'plpgsql'` was being normalised to the bare form, which is a
    respelling sqlalign stopped doing in 1.2 and had kept doing here.
"""
import pytest

from sqlalign.formatter import format_sql, spelling_equal
from sqlalign.spelling import token_census

RESPELT = "output would respell the statement"


def _proc(inner: str) -> str:
    return (f"create or replace procedure p()\nlanguage plpgsql\nas $$\nbegin\n"
            f"  {inner}\nend;\n$$;")


def test_a_respelling_inside_a_body_is_caught():
    """`now()` renders as `CURRENT_TIMESTAMP`: one tree, so the structural check
    cannot see it, and before this the body was exempt from the census too."""
    result = format_sql(_proc("insert into t select now();"), "postgres")
    assert [d.reason for d in result.declines] == [RESPELT]
    assert result.text == _proc("insert into t select now();")


def test_the_same_respelling_outside_a_body_is_recovered_instead():
    """The contrast that shows the body is the gap, not the construct.

    At the top level the same `now()` is caught and then cased from its own
    source, so it formats and keeps its spelling. Inside a body it can only be
    caught -- casing a procedure from source would cost every clause in it the
    layout, for the sake of one.
    """
    result = format_sql("select now();", "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert result.text.strip() == "SELECT NOW();", result.text
    assert "CURRENT_TIMESTAMP" not in result.text, "the respelling came back"


@pytest.mark.parametrize("inner", [
    "delete from t;",
    "insert into t select interval '14 days';",
    "raise notice 'hi';",
    "perform pg_sleep(1);",
])
def test_an_ordinary_procedure_still_formats(inner):
    """The census must not fire on a body being laid out, only on one being
    respelt. This is the half that 102 tests were protecting."""
    result = format_sql(_proc(inner), "postgres")
    assert not result.declines, [d.reason for d in result.declines]


def test_a_declare_is_not_content_just_because_the_tokenizer_says_string():
    """sqlglot types `DECLARE` as a COMMAND and hands back `row_count int` as a
    single STRING token. Compared as content, casing it to `INT` read as a
    rewrite and every procedure with a DECLARE declined."""
    source = ("create function f() returns int as $$\ndeclare row_count int;\nbegin\n"
              "  row_count := 1;\n  return row_count;\nend;\n$$ language plpgsql;")
    result = format_sql(source, "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert "DECLARE row_count INT;" in result.text


def test_a_terminator_the_formatter_added_is_not_a_respelling():
    """A T-SQL `CREATE PROCEDURE ... END` comes back as `END;`. Counting
    terminators reported every such statement as rewritten."""
    assert spelling_equal("SELECT a FROM t", "SELECT a FROM t;", "postgres")
    assert token_census("SELECT a", "postgres") == token_census("SELECT a;", "postgres")


def test_losing_a_terminator_is_still_caught_elsewhere():
    """Dropping `;` from the census is safe because it is not the thing that
    guards statement boundaries: run two statements together and they parse as
    something else entirely."""
    joined = format_sql("select a from t\nselect b from u;", "postgres")
    assert joined.declines, "two statements with no terminator between them formatted"


def test_a_quoted_language_is_not_normalised_away():
    """The last respelling the engine was still performing on its own."""
    source = ("create function g() returns int language 'plpgsql' as $$ begin "
              "return 1; end; $$;")
    result = format_sql(source, "postgres")
    assert not result.declines
    assert "LANGUAGE 'plpgsql'" in result.text


def test_a_body_is_not_cased_from_source_when_it_respells():
    """The 1.3 fallback cases a respelt statement from its source. A procedure is
    excluded: one clause respelling would cost every other clause in the body its
    layout, which is worse than passing the statement through."""
    source = _proc("insert into t select now();")
    result = format_sql(source, "postgres")
    assert result.text == source, "the body was cased from source instead of passed through"
