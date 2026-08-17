"""The guard against sqlalign respelling a statement.

`ast_equal` compares sqlalign's output against its input as a syntax tree, under
the same sqlglot. That is blind to a rewrite sqlglot's own parser performs: two
spellings collapse to one node, so printing either compares equal. Every case
below shipped silently before this check existed, with `declines == ()`.

The division of labour matters and is the reason nothing here is a hand-kept
allowance list:

  * the TREE catches anything that moved, because a different order is a
    different tree;
  * the token CENSUS catches anything added or lost, which the tree cannot see.

So order is deliberately not compared here. `GROUP BY ROLLUP(a, b), c` printed
as `GROUP BY c, ROLLUP(a, b)` is the same grouping sets, and `NOT x IS NULL`
printed as `x IS NOT NULL` is one tree in the dialects that cannot tell the two
apart. A check that fired on those would be a check somebody turned off.
"""
import pytest

from sqlalign.formatter import format_sql, spelling_equal
from sqlalign.spelling import token_census, type_synonyms

RESPELT = "output would respell the statement"


@pytest.mark.parametrize("dialect,sql,what", [
    ("postgres", "ALTER TABLE t ADD COLUMN c INTEGER ARRAY;", "the array is dropped"),
    ("postgres", "DROP FUNCTION f();", "the overload signature is dropped"),
    ("postgres", "COPY t TO '/x.csv' WITH CSV HEADER FORCE QUOTE a, b;",
     "one option becomes three"),
    ("postgres", "ALTER TABLE t ALTER COLUMN x TYPE TEXT;", "respelt SET DATA TYPE"),
    ("tsql", "ALTER TABLE t SET (LOCK_ESCALATION = AUTO);", "a nested WITH is invented"),
    ("tsql", "ALTER TABLE t ADD c DATETIME2 DEFAULT SYSDATETIME();",
     "a different function"),
    ("redshift", "ALTER TABLE t SET (fillfactor = 70);", "becomes SET TABLE PROPERTIES"),
])
def test_a_respelt_statement_is_passed_through(dialect, sql, what):
    """Each of these formatted silently before the guard, changed."""
    result = format_sql(sql, dialect)
    assert [d.reason for d in result.declines] == [RESPELT], what
    assert result.text == sql, "a decline must pass the original through unchanged"


@pytest.mark.parametrize("dialect,sql", [
    ("postgres", "select a from t group by rollup(a, b), c;"),
    ("tsql", "SELECT a FROM t WHERE NOT b IS NULL;"),
])
def test_a_reordering_is_not_a_respelling(dialect, sql):
    """Order belongs to the tree check. Comparing it here too would decline
    normalisations that are provably meaning-preserving."""
    assert not format_sql(sql, dialect).declines


@pytest.mark.parametrize("before,after", [
    ("SELECT a FROM t AS x", "SELECT a FROM t x"),                  # table_alias_style
    ("CREATE TABLE t (a INT)", "CREATE TABLE t (a INTEGER)"),        # type synonym
    ("SELECT a FROM t WHERE x != 1", "SELECT a FROM t WHERE x <> 1"),  # neq_style
    ("select a from t", "SELECT A FROM T"),                          # keyword_case
])
def test_the_settings_own_choices_are_not_respellings(before, after):
    """Three settings exist to choose between spellings. If the guard counted
    those, every formatted file would report as rewritten."""
    assert spelling_equal(before, after, "postgres")


def test_the_synonym_table_is_derived_not_typed():
    """The answer to "how is this list maintained": it is not. Asking sqlglot
    means a new dialect, or a release that renames a type, is followed."""
    for dialect in ("postgres", "redshift", "tsql"):
        synonyms = type_synonyms(dialect)
        assert len(synonyms) > 50, f"{dialect} derived only {len(synonyms)}"
        assert synonyms["INTEGER"] == synonyms["INT"]
        assert synonyms["BOOL"] == synonyms["BOOLEAN"]


def test_bodies_are_left_to_the_structural_check():
    """A `$$` body is one string literal to the tokenizer, so laying it out
    reads as changed tokens. Those statements are compared by
    `_plpgsql_ast_equal` instead, and double-guarding them would decline every
    procedure sqlalign formats."""
    body = ("create function f() returns int as $$ begin return 1; end; $$ "
            "language plpgsql;")
    assert not format_sql(body, "postgres").declines


def test_unreadable_input_does_not_invent_a_reason():
    """Input the tokenizer rejects fails to parse and declines earlier; the
    guard must not add a second, wrong explanation on top."""
    assert spelling_equal("SELECT a /* unterminated", "SELECT a", "postgres")


def test_the_census_ignores_order_but_not_content():
    assert token_census("SELECT a, b", "postgres") == token_census("SELECT b, a", "postgres")
    assert token_census("SELECT a, b", "postgres") != token_census("SELECT a", "postgres")
