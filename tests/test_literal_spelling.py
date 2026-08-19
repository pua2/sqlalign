"""Case inside a string literal: the hole 1.2's guard left, and what fell in it.

1.2 shipped a token census so a statement sqlglot's parser silently respells is
passed through instead. The census uppercased every token before counting,
including string literals -- so a change INSIDE a literal was invisible to it,
and to the other two checks as well:

  * `ast_equal` cannot see it. `INTERVAL '14 days'` parses to
    `Interval(this=Literal('14'), unit=Var(DAYS))`: sqlglot uppercases the unit
    while parsing, so both spellings produce the identical tree.
  * `comments_equal` is about comments.
  * the census had casefolded both sides.

Three checks, none of which could see `INTERVAL '14 days'` going out as
`INTERVAL '14 DAYS'` -- which it did, in every release up to and including 1.2.

Literal content is now compared byte-for-byte, and the renderer puts the
author's spelling back from the source, since the tree no longer holds it.
"""
import pytest

from sqlalign.casing import as_written, render_style, set_source
from sqlalign.formatter import format_sql
from sqlalign.spelling import literal_spellings, token_census
from sqlalign.style import HOUSE

DIALECTS = ("postgres", "redshift")     # T-SQL has no INTERVAL literal


def test_the_census_sees_a_change_inside_a_literal():
    """The hole itself, stated as the property that was missing."""
    a, b = "SELECT 'Hello World'", "SELECT 'HELLO WORLD'"
    assert token_census(a, "postgres") != token_census(b, "postgres")


def test_the_census_still_ignores_keyword_case():
    """What it was casefolding FOR. Keyword case is `Style.keyword_case`'s to
    choose, and a census that compared it would fire on every formatted file."""
    assert (token_census("select a from t", "postgres")
            == token_census("SELECT a FROM t", "postgres"))


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("literal", ["14 days", "14 DAYS", "3 Months", "1 hour"])
def test_an_interval_keeps_the_spelling_it_was_written_with(dialect, literal):
    result = format_sql(f"select interval '{literal}';", dialect)
    assert not result.declines, [d.reason for d in result.declines]
    assert f"'{literal}'" in result.text, result.text


def test_two_intervals_in_one_statement_keep_their_own_spellings():
    result = format_sql("select interval '1 hour', interval '2 WEEKS';", "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert "'1 hour'" in result.text and "'2 WEEKS'" in result.text


def test_a_spelling_does_not_leak_between_statements():
    """The lookup is per statement. Sharing one across a file would let the
    first `interval '5 days'` decide how the last one is spelt."""
    result = format_sql("select interval '5 days';\nselect interval '5 DAYS';\n", "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert "'5 days'" in result.text and "'5 DAYS'" in result.text


@pytest.mark.parametrize(("clause", "literal"), [
    pytest.param("perform interval '2 hours'", "2 hours", id="plain-clause"),
    pytest.param("insert into t select interval '14 days'", "14 days", id="sql-statement"),
])
def test_an_interval_inside_a_procedure_body_keeps_its_spelling(clause, literal):
    """A `$$ ... $$` body is one HEREDOC token to the tokenizer, so harvesting
    the statement's literals found nothing inside it. Only the SQL-statement case
    showed it: a plain clause is keyword-cased by the procedural renderer and
    never reaches sqlglot's generator, so it kept its spelling by not being
    touched -- and the first version of this test picked that one.
    """
    body = (f"create function f() returns int as $$\nbegin\n"
            f"  {clause};\n  return 1;\nend;\n$$ language plpgsql;")
    result = format_sql(body, "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert f"'{literal}'" in result.text, result.text


def test_literals_are_harvested_from_inside_a_body():
    src = ("create procedure p() language plpgsql as $$\nbegin\n"
           "  insert into t select interval '14 days';\nend;\n$$;")
    assert literal_spellings(src, "postgres") == {"14 DAYS": "14 days"}


def test_a_literal_the_source_never_wrote_is_never_substituted():
    """`as_written` may only restore case. A miss returns its argument, so no
    substitution can introduce content the author did not write."""
    with render_style(HOUSE):
        set_source("select '14 days'", "postgres")
        assert as_written("14 DAYS") == "14 days"
        assert as_written("14 WEEKS") == "14 WEEKS", "invented a spelling from nowhere"
        assert as_written("") == ""


@pytest.mark.parametrize(("sql", "expected"), [
    pytest.param("select interval '5 days', interval '5 DAYS'", {}, id="top-level"),
    pytest.param("create procedure p() language plpgsql as $$begin\n"
                 "  perform interval '5 days';\n  perform interval '5 DAYS';\nend;$$",
                 {}, id="inside-a-body"),
    pytest.param("select interval '5 days', interval '2 HOURS'",
                 {"5 DAYS": "5 days", "2 HOURS": "2 HOURS"}, id="unambiguous-pair"),
])
def test_a_literal_written_two_ways_offers_no_spelling(sql, expected):
    """Either choice respells the other, so neither is offered and sqlglot's own
    output stands. It matters inside a `$$` body, which is exempt from the token
    census: a wrong substitution there has nothing downstream to catch it."""
    assert literal_spellings(sql, "postgres") == expected


def test_a_body_is_still_exempt_from_the_census():
    """Not an oversight, and the reason the guard above is needed. Laying out a
    body changes its tokens for reasons that are not rewrites -- keyword casing
    reaching what the tokenizer reads as content, a terminator added to a clause
    that had none -- so bodies are compared structurally instead. Dropping the
    exemption turns 102 tests red."""
    from sqlalign.formatter import _has_body

    assert _has_body("create function f() returns int as $$select 1$$ language sql;",
                     "postgres")
    assert not _has_body("select interval '5 days';", "postgres")


def test_the_source_map_holds_only_string_literals():
    """A quoted identifier is not a string literal and is not a source of
    spellings for one."""
    spellings = literal_spellings("""select x as "Total Days", 'two days' from t""",
                                  "postgres")
    assert spellings == {"TWO DAYS": "two days"}
