"""`FETCH FIRST` and `LATERAL (SELECT …)` — the last two constructs on the list.

`FETCH FIRST n ROWS ONLY` is the ANSI spelling of `LIMIT`, which makes it
all-or-nothing rather than an edge case: a shop either never writes it or writes
it in every query. `OFFSET` precedes it in that form rather than following it the
way it follows `LIMIT`, so it takes its own line.

`LATERAL (SELECT …)` is a derived table whose geometry is identical to any other
— the only reason it could not simply fall through is that its **alias hangs off
the `Lateral` rather than off the subquery**, so every read of
`subquery.args["alias"]` came back empty.

Both spellings work, and the comma form is the common one, which is why this had
to wait for comma joins to be emitted at all.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- FETCH FIRST ---------------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select a from t order by a fetch first 5 rows only;",
     "ORDER BY a\nFETCH FIRST 5 ROWS ONLY;"),
    ("select a from t order by a fetch first 10 percent rows only;",
     "FETCH FIRST 10 PERCENT ROWS ONLY;"),
    ("select a from t order by a fetch first 5 rows with ties;",
     "FETCH FIRST 5 ROWS WITH TIES;"),
])
def test_the_fetch_variants(sql, expect):
    assert fmt(sql).endswith(expect)


def test_offset_precedes_fetch_on_its_own_line():
    """The ANSI form is `OFFSET n ROWS FETCH NEXT m ROWS ONLY` — the opposite
    order from `LIMIT n OFFSET m`, which share one line."""
    assert fmt("select a from t offset 10 fetch next 5 rows only;") == (
        "SELECT a\nFROM t\nOFFSET 10\nFETCH NEXT 5 ROWS ONLY;"
    )


def test_limit_and_offset_still_share_a_line():
    """The pre-existing shape must not have moved."""
    assert fmt("select a from t limit 5 offset 10;").endswith("LIMIT 5 OFFSET 10;")


def test_fetch_is_cased_like_everything_else():
    out = fmt("select a from t order by a FETCH FIRST 5 ROWS ONLY;",
              Style(keyword_case="lower"))
    assert out.endswith("fetch first 5 rows only;"), out


# ---- LATERAL (SELECT ...) ------------------------------------------------

def test_the_comma_form():
    assert fmt("select 1 from a, lateral (select x from b) c;") == (
        "SELECT 1\n"
        "FROM a\n"
        "   , LATERAL (SELECT x\n"
        "              FROM b\n"
        "             ) c;"
    )


def test_the_cross_join_form():
    assert fmt("select 1 from a cross join lateral (select x from b) c;") == (
        "SELECT 1\n"
        "FROM a\n"
        "CROSS JOIN LATERAL (SELECT x\n"
        "                    FROM b\n"
        "                   ) c;"
    )


def test_a_correlated_lateral():
    """The whole point of LATERAL — the subquery references the row to its left."""
    out = fmt("select o.id, t.total from orders o, "
              "lateral (select sum(amount) as total from items where items.oid = o.id) t;")
    assert "WHERE items.oid = o.id" in out
    assert out.endswith(") t;"), out


def test_the_function_form_is_unaffected():
    assert fmt("select 1 from t, lateral unnest(t.tags) as e;").endswith(
        "FROM t\n   , LATERAL UNNEST(t.tags) e;")


def test_a_plain_derived_table_is_unaffected():
    assert fmt("select 1 from a x join (select q from z) d on d.q = x.q;").endswith(
        "JOIN (SELECT q\n      FROM z\n     ) d ON d.q = x.q;")


# ---- invariants ----------------------------------------------------------

SHAPES = [
    "select a from t order by a fetch first 5 rows only;",
    "select a from t offset 10 fetch next 5 rows only;",
    "select 1 from a, lateral (select x from b) c;",
    "select 1 from a cross join lateral (select x from b) c;",
]


@pytest.mark.parametrize("sql", SHAPES)
def test_semantics_and_idempotence(sql):
    out = fmt(sql)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out) == out


@pytest.mark.parametrize("sql", SHAPES)
@pytest.mark.parametrize("preset", ["compact", "gitlab", "river"])
def test_they_compose_with_the_presets(sql, preset):
    style = preset_style(preset)
    out = fmt(sql, style)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out, style) == out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


# ---- PIVOT declines under postgres, and only there ------------------------

@pytest.mark.parametrize("sql", [
    "select * from t pivot (sum(x) for y in (1, 2)) p;",
    "select * from t unpivot (x for y in (a, b)) u;",
])
def test_pivot_declines_under_postgres(sql):
    """Not because `exp.Pivot` is complicated -- that was the old reasoning, and
    it was beside the point, since nothing has to REBUILD the node. Because
    PIVOT is not Postgres syntax: sqlglot's postgres generator DROPS the clause
    silently, and `SELECT * FROM t PIVOT(...)` comes back as `SELECT * FROM t`
    with the whole thing gone. See tests/test_pivot.py, where it formats under
    the dialects that have it.
    """
    result = format_sql(sql, "postgres")
    assert result.warnings
    assert "this dialect has no such syntax" in result.declines[0].reason
    assert result.text.strip() == sql.strip()
