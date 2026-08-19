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

**What the guard does about a respelling changed in 1.3 and its guarantee did
not.** These statements used to pass through untouched. Now the engine falls
back to casing them from their own source, which cannot lose a spelling because
it never rebuilds anything -- so they format, and still come out spelt as
written. The property asserted here is the one that matters either way: what
comes out is never a respelling of what went in.
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
def test_a_statement_the_generator_would_respell_comes_out_as_written(dialect, sql, what):
    """Each of these formatted silently before the guard, changed.

    Two assertions, and the first is the one that would rot: the generator must
    still be respelling these. If sqlglot ever stops, this case has left the
    category and the fallback below is no longer what is being exercised.
    """
    import sqlglot
    assert sqlglot.parse_one(sql, dialect=dialect).sql(dialect=dialect) + ";" != sql, (
        f"sqlglot no longer respells this ({what}): the case has moved")

    result = format_sql(sql, dialect)
    assert not result.declines, [d.reason for d in result.declines]
    assert spelling_equal(sql, result.text, dialect), f"{what}: {result.text}"


@pytest.mark.parametrize(("dialect", "sql"), [
    ("postgres", "SET ROLE reporting;"),
    ("postgres", "RESET ROLE;"),
])
def test_a_command_still_passes_through(dialect, sql):
    """The fallback needs a parse to tell a keyword from an identifier. An
    `exp.Command` is raw text with no content nodes, so every word in it would
    read as grammar and `SET ROLE reporting` would come out `SET ROLE
    REPORTING` -- a renamed role. Those keep passing through."""
    result = format_sql(sql, dialect)
    assert result.declines, "a Command must not be cased from source"
    assert result.text == sql, "a decline must pass the original through unchanged"


@pytest.mark.parametrize(("dialect", "sql", "expected"), [
    ("postgres", "alter table t alter column x type text;",
     "ALTER TABLE t ALTER COLUMN x TYPE TEXT;"),
    ("postgres", "drop function f();", "DROP FUNCTION f();"),
    ("postgres", "alter table t add column c integer array;",
     "ALTER TABLE t ADD COLUMN c INTEGER ARRAY;"),
    ("postgres", "set search_path to public;", "SET search_path TO public;"),
    ("postgres", "set local work_mem = '64MB';", "SET LOCAL work_mem = '64MB';"),
])
def test_the_fallback_cases_keywords_and_nothing_else(dialect, sql, expected):
    """It is a formatter, not a passthrough with extra steps: the keywords come
    out cased. Identifiers keep the case the author gave them -- `search_path`
    and `work_mem` are the author's words and stay lowercase."""
    result = format_sql(sql, dialect)
    assert not result.declines, [d.reason for d in result.declines]
    assert result.text.strip() == expected


@pytest.mark.parametrize("sql", [
    "alter table t add column year int;",          # `year` is a keyword word
    "alter table t add column name text;",
    "alter table t add column value numeric;",
    'alter table "Type" add column "Order" text;',  # quoted: case is meaning
])
def test_an_identifier_that_looks_like_a_keyword_is_left_alone(sql):
    """The reason the fallback reads the tree instead of the keyword table. A
    column named `year` is in the tree; the `ADD COLUMN` around it is not."""
    result = format_sql(sql, "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    for word in ("year", "name", "value", '"Type"', '"Order"'):
        if word in sql:
            assert word in result.text, f"{word} was recased: {result.text}"


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
