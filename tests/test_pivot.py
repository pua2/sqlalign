"""PIVOT and UNPIVOT — the last decided decline, reversed.

The reasoning for declining was that `exp.Pivot` carries fifteen args, five of
them populated on the simplest example, and that the syntax diverges across
T-SQL and Snowflake — a sub-project rather than a construct.

Every word of that is true and none of it mattered, because **nothing here has
to rebuild the node**. A pivot hangs off a table's `pivots` arg, so `table_name`
renders it along with the table and sqlglot spells whichever dialect's form
applies. The same mistake as the comma join: the reasoning was right about the
approach it assumed and wrong about the option set.

What DID need checking is whether the dialect has the syntax at all. sqlglot's
postgres generator drops a pivot **silently** — `SELECT * FROM t PIVOT(...)`
comes back as `SELECT * FROM t`, the entire clause gone, with only the re-parse
guard between that and shipping a query that means something else. So the
decline survives for Postgres, and it is detected by rendering rather than by a
hardcoded dialect list — if sqlglot ever grows postgres support this stops
declining by itself.
"""
import pytest

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, dialect, style=None):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- it formats where the dialect has it ----------------------------------

@pytest.mark.parametrize("dialect", ["redshift", "tsql"])
def test_pivot_on_a_table(dialect):
    assert fmt("select * from t pivot (sum(x) for y in (1, 2)) p;", dialect) == (
        "SELECT *\nFROM t PIVOT(SUM(x) FOR y IN (1, 2)) AS p;")


@pytest.mark.parametrize("dialect", ["redshift", "tsql"])
def test_unpivot_on_a_table(dialect):
    assert fmt("select * from t unpivot (x for y in (a, b)) u;", dialect) == (
        "SELECT *\nFROM t UNPIVOT(x FOR y IN (a, b)) AS u;")


def test_a_pivoted_derived_table():
    """The ordinary way to write one: you pivot a projection, not a bare table.
    The subquery keeps its own geometry and the pivot is a suffix on the closing
    line, next to the alias."""
    assert fmt("select * from (select a, b, c from src) s "
               "pivot (avg(c) for b in (1, 2)) p;", "redshift") == (
        "SELECT *\n"
        "FROM (SELECT a\n"
        "           , b\n"
        "           , c\n"
        "      FROM src\n"
        "     ) s PIVOT(AVG(c) FOR b IN (1, 2)) AS p;"
    )


def test_a_pivoted_derived_table_in_join_position():
    out = fmt("select * from t join (select a, b from src) s "
              "pivot (avg(b) for a in (1, 2)) p on p.x = t.x;", "redshift")
    assert out.endswith("     ) s PIVOT(AVG(b) FOR a IN (1, 2)) AS p ON p.x = t.x;"), out


def test_tsql_bracket_quoting_survives():
    """Each dialect spells its own form — that was the divergence the old
    decision worried about, and it is sqlglot's job, not this layer's."""
    assert "IN ([x], [y])" in fmt(
        "select * from t pivot (sum(c) for b in ([x],[y])) p;", "tsql")


# ---- and declines where it does not ---------------------------------------

@pytest.mark.parametrize("sql", [
    "select * from t pivot (sum(x) for y in (1, 2)) p;",
    "select * from t unpivot (x for y in (a, b)) u;",
])
def test_postgres_declines_explicitly(sql):
    result = format_sql(sql, "postgres")
    assert result.warnings
    assert "this dialect has no such syntax" in result.declines[0].reason
    assert result.text.strip() == sql.strip()


def test_the_postgres_generator_really_does_drop_it():
    """The premise of that decline, asserted directly. If this ever stops being
    true, the detection unblocks postgres on its own and this test says so."""
    import sqlglot

    tree = sqlglot.parse_one("select * from t pivot (sum(x) for y in (1, 2)) p",
                             read="postgres")
    assert "PIVOT" not in tree.sql("postgres").upper(), (
        "postgres renders PIVOT now; this decline should have lifted itself")


def test_the_decline_is_detected_not_hardcoded():
    """No dialect name in the check itself — it renders the node and looks.

    Reads the guard's own source rather than a window of text around it, so a
    comment mentioning a dialect cannot fail this and a real hardcoded branch
    cannot slip past by sitting a few lines further down.
    """
    import inspect
    import re

    from sqlalign.layout import select

    body = inspect.getsource(select._guard)
    code = "\n".join(line.split("#")[0] for line in body.split("\n"))
    pivot = [line for line in code.split("\n") if "Pivot" in line]
    assert pivot, "the PIVOT check has moved out of _guard"
    assert not re.search(r"postgres|redshift|tsql", "\n".join(pivot)), (
        f"the check grew a hardcoded dialect: {pivot}")


# ---- invariants -----------------------------------------------------------

SHAPES = [
    "select * from t pivot (sum(x) for y in (1, 2)) p;",
    "select * from t unpivot (x for y in (a, b)) u;",
    "select * from (select a, b, c from src) s pivot (avg(c) for b in (1, 2)) p;",
]


@pytest.mark.parametrize("sql", SHAPES)
def test_semantics_and_idempotence(sql):
    out = fmt(sql, "redshift")
    assert ast_equal(sql, out, "redshift")
    assert fmt(out, "redshift") == out


@pytest.mark.parametrize("sql", SHAPES)
@pytest.mark.parametrize("preset", ["compact", "gitlab", "river"])
def test_they_compose_with_the_presets(sql, preset):
    style = preset_style(preset)
    out = fmt(sql, "redshift", style)
    assert ast_equal(sql, out, "redshift")
    assert fmt(out, "redshift", style) == out


def test_keyword_case_reaches_it():
    out = fmt("select * from t pivot (sum(x) for y in (1, 2)) p;", "redshift",
              Style(keyword_case="lower"))
    assert out.endswith("from t pivot(sum(x) for y in (1, 2)) as p;"), out
