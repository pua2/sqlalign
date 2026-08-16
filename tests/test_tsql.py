"""T-SQL / SQL Server dialect.

Scoped narrowly and deliberately. Two silent-corruption classes were found while
adding it, and the guiding rule is that anything not verified DECLINES to a
byte-identical passthrough rather than being guessed:

  1. KEYWORDS — handlers emit dialect-agnostic literals, which is true for
     postgres/redshift (shared syntax) and false for T-SQL. `LIMIT` is the one that
     matters: sqlglot parses T-SQL `TOP n` into the same Limit node, so emitting
     `LIMIT` produced SQL that SQL Server rejects — and sqlglot parses `LIMIT` back
     as T-SQL too, so ast_equal compared EQUAL and never noticed.
  2. TYPES — sqlglot collapses REAL/FLOAT and NTEXT/TEXT to one node AT PARSE TIME.
     `REAL` is FLOAT(24) and `FLOAT` is FLOAT(53); `NTEXT` is Unicode and `TEXT` is
     not. The distinction is gone before the AST exists, so ast_equal cannot help
     and only a raw-source guard can.
"""
import re

import pytest
import sqlglot

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.splitter import split_statements
from sqlalign.style import SUPPORTED_DIALECTS


def _fmt(sql):
    """Every test here is the house style under tsql; only the dialect varies."""
    return format_sql(sql, "tsql")


# ---- TOP ---------------------------------------------

def test_top_rides_the_select_line_with_commas_at_column_7():
    """continuation commas keep their usual column 7 rather than
    aligning under the first item, because the comma column is a fixed house
    constant, not derived from where the first item happens to start."""
    out = _fmt("select top 10 id, name, email from users order by id;").text
    assert out == ("SELECT TOP 10 id\n"
                   "     , name\n"
                   "     , email\n"
                   "FROM users\n"
                   "ORDER BY id;")


def test_limit_is_never_emitted_for_tsql():
    """The original corruption. `LIMIT` is not valid T-SQL."""
    for sql in ("select top 5 a from t;",
                "select top 100 a from t where b = 1 order by a;"):
        assert "LIMIT" not in _fmt(sql).text.upper()


def test_top_survives_a_round_trip():
    sql = "select top 10 id from users order by id;"
    once = _fmt(sql).text
    assert _fmt(once).text == once
    assert ast_equal(sql, once, "tsql")


def test_postgres_limit_is_unaffected():
    """The T-SQL branch must not leak into the shipped dialects."""
    out = format_sql("select a from t order by a limit 10;", "postgres").text
    assert out.endswith("LIMIT 10;")


# ---- the parse-time type collapses decline --------------------------------

@pytest.mark.parametrize("sql", [
    "select cast(x as real) from t;",
    "create table t (a real not null);",
    "create table t (a ntext);",
    "select cast(x as NTEXT) from t;",
])
def test_lossy_types_decline_to_passthrough(sql):
    """REAL/NTEXT lose their meaning through sqlglot's parse, and ast_equal cannot
    see it, so the statement must pass through byte-identical instead."""
    result = _fmt(sql)
    assert result.text == sql
    assert result.warnings


def test_synonym_types_still_format():
    """INT/INTEGER and DECIMAL/NUMERIC also collapse, but they ARE synonyms, so
    canonicalising them is the same call the house style already makes."""
    result = _fmt("select cast(x as int) from t;")
    assert result.warnings == []
    assert "CAST(x AS INTEGER)" in result.text


# ---- casts: T-SQL has no `::` ---------------------------------------------

def test_casts_render_as_cast_not_double_colon():
    """`::` is postgres syntax and invalid in T-SQL; the house cast mixin must not
    apply its shorthand here."""
    out = _fmt("select cast(x as int) from t;").text
    assert "::" not in out
    assert "CAST(" in out


def test_postgres_double_colon_still_preserved():
    out = format_sql("select x::numeric from t;", "postgres").text
    assert "::NUMERIC" in out


# ---- splitter: bracket-quoted identifiers ---------------------------------

def test_bracketed_identifier_containing_a_semicolon_does_not_split():
    src = "SELECT [my;col] FROM t; SELECT 2;"
    parts = split_statements(src)
    assert len(parts) == 2
    assert "".join(parts) == src            # still lossless


def test_bracketed_identifier_with_escaped_bracket():
    src = "SELECT [odd]]name] FROM t; SELECT 2;"
    parts = split_statements(src)
    assert len(parts) == 2
    assert "".join(parts) == src


def test_bracketed_identifiers_are_preserved_verbatim():
    """Consistent with the standing promise that identifiers are never touched."""
    sql = "select [Order Id], [Name] from [My Table];"
    out = _fmt(sql).text
    for token in ("[Order Id]", "[Name]", "[My Table]"):
        assert token in out


def test_brackets_do_not_affect_the_other_dialects():
    assert len(split_statements("select 'a;b' from t; select 2;")) == 2


# ---- ordinary queries format ----------------------------------------------

def test_join_query_formats_with_alignment():
    sql = ("select c.id, c.email, o.total from customers c "
           "inner join orders o on o.customer_id = c.id "
           "where c.status = 'active' and o.total > 100;")
    out = _fmt(sql).text
    # the FROM-block alias column aligns across both table rows
    from_line = next(x for x in out.split("\n") if x.startswith("FROM"))
    join_line = next(x for x in out.split("\n") if x.startswith("INNER JOIN"))
    # The alias is the whitespace-delimited token following the table name.
    from_alias = re.search(r"\S+\s+\S+(\s+)(\S+)$", from_line)
    join_alias = re.search(r"^INNER JOIN \S+(\s+)(\S+) ON", join_line)
    assert from_alias and join_alias
    assert from_alias.start(2) == join_alias.start(2), (from_line, join_line)
    assert "ON o.customer_id = c.id" in out
    assert ast_equal(sql, out, "tsql")


def test_group_by_and_having():
    sql = ("select channel, count(*) from orders "
           "group by channel having count(*) > 5;")
    out = _fmt(sql)
    assert out.warnings == []
    assert ast_equal(sql, out.text, "tsql")


# ---- paging --------------------------------------------------------------

def test_tsql_paging_formats():
    """`OFFSET n ROWS FETCH NEXT m ROWS ONLY` is T-SQL's own paging syntax, and
    it is the SAME geometry Postgres already had -- one shared FETCH path.

    It used to decline, supposedly because the geometry was unverified. It was
    not declining: it was CRASHING. `Fetch` lands in the `limit` arg exactly as
    `Limit` does, the TOP branch took it for a LIMIT and read `limit.expression`
    (a Fetch keeps its count in `count`), and the AttributeError fired one line
    ABOVE the explicit decline meant to catch it -- so the statement came back
    as `internal formatter error`, the wording reserved for a real bug.
    """
    out = _fmt("select a from t order by a offset 10 rows fetch next 5 rows only;")
    assert out.warnings == []
    assert out.text == (
        "SELECT a\n"
        "FROM t\n"
        "ORDER BY a\n"
        "OFFSET 10\n"
        "FETCH NEXT 5 ROWS ONLY;"
    )


def test_top_is_still_top():
    """Only an `exp.Limit` becomes TOP; the Fetch path must not have taken it."""
    assert _fmt("select top 10 a from t;").text == "SELECT TOP 10 a\nFROM t;"


def test_top_with_offset_declines():
    """`SELECT TOP n ... OFFSET m` is not valid T-SQL -- paging is spelled
    OFFSET/FETCH. This is the decline the crash was standing in front of."""
    result = _fmt("select top 5 a from t order by a offset 10 rows;")
    assert result.warnings
    assert "TOP with OFFSET" in result.declines[0].reason


def test_tsql_is_a_supported_dialect():
    # The CLI's --dialect choices are asserted to equal SUPPORTED_DIALECTS exactly,
    # in test_dialect_guard.test_cli_offers_only_verified_dialects, so membership
    # here is also the statement that `--dialect tsql` is offered.
    assert "tsql" in SUPPORTED_DIALECTS


# ---- control flow: not a gap, an upstream parse limit ---------------------

@pytest.mark.parametrize("body", [
    "if @x > 1 begin select 1; end",
    "while @x > 1 begin select 1; end",
    "if @x > 1 begin select 1; end else begin select 2; end",
])
def test_control_flow_declines_because_sqlglot_leaves_it_unparsed(body):
    """Not a modelling gap. The moment an `IF` or `WHILE` appears, part of the
    body comes back as `exp.Command` holding raw source TEXT rather than a tree.
    """
    from sqlglot import exp

    sql = f"create procedure p as begin {body} end"
    block = sqlglot.parse_one(sql, read="tsql").args["expression"]
    assert any(isinstance(n, exp.Command) for n in block.walk()), (
        "sqlglot parses this now; the decline should have lifted itself")
    result = _fmt(sql + ";")
    assert result.warnings
    assert "left part of the body unparsed" in result.declines[0].reason


def test_the_else_branch_is_the_reason_it_cannot_be_partially_modelled():
    """An `IfBlock` with an ELSE and one without are indistinguishable at the
    node — the ELSE arrives as a raw string INSIDE the true-branch block. Laying
    out what is in the tree would drop it silently."""
    from sqlglot import exp

    with_else = sqlglot.parse_one(
        "create procedure p as begin if @x > 1 begin select 1; end "
        "else begin select 2; end end", read="tsql")
    texts = [c.args["expression"].name for c in with_else.find_all(exp.Command)
             if c.args.get("expression") is not None]
    assert any("else" in t for t in texts), texts
    assert not any(isinstance(n, exp.If) and n.args.get("false") for n in with_else.walk())


def test_a_plain_body_has_no_unparsed_remainder():
    """The check keys on the remainder, not on a list of node types — so a body
    sqlglot fully parses is unaffected."""
    from sqlglot import exp

    block = sqlglot.parse_one("create procedure p as begin select 1; end",
                              read="tsql").args["expression"]
    assert not any(isinstance(n, exp.Command) for n in block.walk())
    assert _fmt("create procedure p as begin select 1; end").warnings == []
