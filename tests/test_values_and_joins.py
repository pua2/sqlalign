"""`INSERT ... VALUES`, `CROSS JOIN` and `NATURAL JOIN` — three shapes that used
to pass through untouched.

`VALUES` reached `layout_statement` with no handler at all: `_insert_lines`
accepted an `exp.Values` body and then handed it to a dispatch that declined it,
so every `INSERT ... VALUES` in a file was left alone.

The joins were declined by a guard reading `has_on == has_using`, which lumped
"both" (genuinely undefined) together with "neither". "Neither" is ordinary SQL
when the join says so — `CROSS JOIN`, `NATURAL JOIN`.

The interesting half is what stays declined. `FROM a, b` ALSO parses as a
conditionless join, and rebuilding it through `join_keyword` would emit
`FROM a JOIN b` — which Postgres rejects, because a bare `JOIN` requires a
condition. The re-parse safety net cannot catch that: sqlglot parses its own
lenient output back happily. So the guard is the only thing standing between a
comma join and invalid SQL, and it is tested here as carefully as the features.
"""
import pytest
import sqlglot
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


def declines(sql, dialect="postgres"):
    result = format_sql(sql, dialect)
    return bool(result.warnings) and result.text.strip() == sql.strip()


# ---- INSERT ... VALUES ---------------------------------------------------

def test_single_row_stays_inline():
    assert fmt("insert into t (a, b) values (1, 2);") == (
        "INSERT INTO t\n"
        "(  a\n"
        " , b)\n"
        "VALUES (1, 2);"
    )


def test_multiple_rows_stack_with_leading_commas():
    """`VALUES ` is the same width as `SELECT `, so the commas land in the house
    column with no special case."""
    assert fmt("insert into t (a, b) values (1, 'a'), (2, 'b'), (3, 'c');") == (
        "INSERT INTO t\n"
        "(  a\n"
        " , b)\n"
        "VALUES (1, 'a')\n"
        "     , (2, 'b')\n"
        "     , (3, 'c');"
    )


def test_without_a_column_list():
    assert fmt("insert into t values (1, 2);") == "INSERT INTO t\nVALUES (1, 2);"


def test_values_honours_comma_position():
    out = fmt("insert into t (a) values (1), (2);", Style(comma_position="trailing"))
    assert "VALUES (1),\n       (2);" in out, out


def test_insert_select_still_works():
    """The path that already worked must not regress."""
    assert fmt("insert into t (a) select a from u;") == (
        "INSERT INTO t\n"
        "(  a)\n"
        "SELECT a\n"
        "FROM u;"
    )


# ---- CROSS / NATURAL joins ------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select 1 from a cross join b;", "FROM a\nCROSS JOIN b;"),
    ("select 1 from a natural join b;", "FROM a\nNATURAL JOIN b;"),
    ("select 1 from a natural left join b;", "FROM a\nNATURAL LEFT JOIN b;"),
])
def test_conditionless_joins_format(sql, expect):
    assert expect in fmt(sql)


def test_natural_keeps_its_keyword():
    """`method` used to be an exotic arg, so a NATURAL join declined. Emitting
    the keyword without NATURAL would change which rows come back — and both
    spellings parse, so the AST check would not have flagged it."""
    out = fmt("select 1 from a natural join b;")
    assert "NATURAL JOIN" in out
    assert ast_equal("select 1 from a natural join b;", out, "postgres")


def test_a_conditionless_join_mixes_with_a_normal_one():
    out = fmt("select 1 from a x cross join b y join c z on z.id = x.id;")
    assert "CROSS JOIN b y" in out
    assert "JOIN c" in out and "ON z.id = x.id" in out


# ---- what stays declined, and why ----------------------------------------

def test_the_comma_join_is_emitted_as_a_comma():
    """It used to decline. The decline was right given the approach it assumed —
    rebuilding through `join_keyword` — but emitting the COMMA is the option that
    was missed, and it is both valid and the author's own syntax."""
    assert fmt("select 1 from a x, b y where x.id = y.id;") == (
        "SELECT 1\n"
        "FROM a x\n"
        "   , b y\n"
        "WHERE x.id = y.id;"
    )


def test_rebuilding_a_comma_join_through_join_keyword_would_be_invalid():
    """Why the comma form is emitted rather than a keyword. `FROM a, b` is a
    conditionless join, so `join_keyword` rebuilds it as a bare `JOIN` — and
    Postgres rejects `JOIN` with no condition. The re-parse check cannot catch
    that, because sqlglot reads its own lenient output back happily."""
    from sqlalign.layout import join_keyword

    tree = sqlglot.parse_one("select 1 from a, b", read="postgres")
    join = tree.args["joins"][0]
    assert join.args.get("on") is None and not join.args.get("using")
    assert join_keyword(join) == "JOIN", "a comma join rebuilds as a bare JOIN"
    assert tree.sql("postgres") == "SELECT 1 FROM a, b", "sqlglot prints the comma form"


def test_both_on_and_using_declines():
    """Genuinely undefined, and distinct from 'neither'."""
    sql = "select 1 from a join b on b.id = a.id using (id);"
    try:
        result = format_sql(sql, "postgres")
    except Exception:
        pytest.skip("sqlglot rejects this at parse time")
    assert result.warnings


# ---- invariants ----------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "insert into t (a, b) values (1, 2), (3, 4);",
    "select 1 from a cross join b;",
    "select 1 from a natural join b;",
])
def test_semantics_and_idempotence(sql):
    out = fmt(sql)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out) == out


def test_composes_with_the_river():
    out = fmt("select 1 from a cross join b where a.id > 0;", preset_style("river"))
    assert "\n       CROSS JOIN b" in out, out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected
