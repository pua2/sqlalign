"""`NOT x IS NULL`, which the renderer used to rewrite.

`NOT x IS NULL` and `x IS NOT NULL` mean the same thing, and the layout printed
the second whichever the author wrote. That is a rewrite, not a reformat — the
tool's contract is that presentation changes and nothing else — and the re-parse
guard agreed: it saw a different tree and passed the statement through, so under
Postgres every `NOT ... IS NULL` silently stopped being formatted.

Whether the two spellings can be told apart is a property of the dialect, not of
sqlalign:

    postgres   x IS NOT NULL -> Is(negate=True)    NOT x IS NULL -> Not(Is)
    redshift   both -> Not(Is)
    tsql       both -> Not(Is)

So Postgres can preserve what was written and the other two cannot — the
distinction is gone before the layout sees the tree. Where it is gone, the
idiomatic spelling is the only sensible choice; where it survives, rewriting it
would be inventing a preference the author did not express.

Found by the corpus suite: sqlglot's optimizer emits this shape.
"""
import pytest

from sqlalign.formatter import format_sql
from sqlalign.layout.conditions import _keeps_negated_is_apart


def test_which_dialects_can_tell_the_two_spellings_apart():
    """The premise of everything below, asserted rather than assumed. If sqlglot
    changes shape upstream, this fails first and explains the rest."""
    assert _keeps_negated_is_apart("postgres") is True
    assert _keeps_negated_is_apart("redshift") is False
    assert _keeps_negated_is_apart("tsql") is False


@pytest.mark.parametrize("written", ["NOT b IS NULL", "b IS NOT NULL"])
def test_postgres_prints_back_what_was_written(written):
    result = format_sql(f"SELECT a FROM t WHERE {written};", "postgres")
    assert not result.warnings, result.warnings
    assert written in result.text, result.text


@pytest.mark.parametrize("dialect", ["redshift", "tsql"])
@pytest.mark.parametrize("written", ["NOT b IS NULL", "b IS NOT NULL"])
def test_the_other_dialects_settle_on_the_idiomatic_spelling(dialect, written):
    """Both spellings parse to one tree here, so neither can be recovered. What
    matters is that the statement formats rather than declining."""
    result = format_sql(f"SELECT a FROM t WHERE {written};", dialect)
    assert not result.warnings, result.warnings
    assert "b IS NOT NULL" in result.text, result.text


@pytest.mark.parametrize("written", ["b IS NOT TRUE", "b IS NOT FALSE",
                                     "NOT b IS NOT NULL"])
def test_the_boolean_forms_and_the_double_negation_print_back(written):
    """The two regressions the first version of this fix shipped.

    `x IS NOT TRUE` and `NOT x IS TRUE` are ONE tree even in Postgres -- the
    probe's answer only holds for a NULL right-hand side -- so treating booleans
    like NULL rewrote the idiomatic spelling into `NOT x IS TRUE`, and nothing
    could notice because both spellings compare equal.

    And `NOT x IS NOT NULL` parses to Not(Is(negate)): dropping the inner negate
    rendered the logical INVERSE, which only the re-parse guard stopped."""
    result = format_sql(f"SELECT a FROM t WHERE {written};", "postgres")
    assert not result.warnings, result.warnings
    assert written in " ".join(result.text.split()), result.text


def test_it_survives_a_second_pass():
    """The rewrite made this non-obvious: `NOT b IS NULL` becoming
    `b IS NOT NULL` would have been stable, but only by changing the input."""
    once = format_sql("SELECT a FROM t WHERE NOT b IS NULL;", "postgres").text
    assert format_sql(once, "postgres").text == once


def test_a_negated_is_inside_a_larger_predicate():
    """The shape found in the corpus, where it sat behind an AND after a CASE.

    Compared with whitespace collapsed: the operator column aligns `IS` against
    the CASE expression on the line above, so the rendered predicate is padded
    apart rather than contiguous.
    """
    sql = ("SELECT a FROM t WHERE CASE WHEN b IS NULL THEN 0 ELSE 1 END = FALSE "
           "AND NOT b IS NULL;")
    result = format_sql(sql, "postgres")
    assert not result.warnings, result.warnings
    assert "AND NOT b IS NULL" in " ".join(result.text.split()), result.text


@pytest.mark.parametrize("predicate", [
    "NOT EXISTS (SELECT 1 FROM u)",
    "b NOT IN (1, 2)",
    "b NOT BETWEEN 1 AND 2",
    "NOT (b AND c)",
])
def test_other_negations_are_untouched(predicate):
    """The new branch is narrow -- a `Not` directly wrapping an `Is`. Every other
    negation must print back as written, or the fix has widened into a rewrite of
    its own."""
    result = format_sql(f"SELECT a FROM t WHERE {predicate};", "postgres")
    assert not result.warnings, result.warnings
    assert predicate in " ".join(result.text.split()), result.text
