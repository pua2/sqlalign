"""Comma joins, LATERAL, and — the one that mattered most — user-defined
functions.

The UDF bug was found while chasing LATERAL and is much the larger of the two.
**Every lowercase call to a function sqlglot has no node for was silently
declined**, which is every user-defined function in every repository:

    select my_custom_func(a, b) from t;   -- passed through, untouched

sqlglot parses an unknown function as `exp.Anonymous`, whose `this` is the
function NAME as a case-preserved plain string. sqlalign cases function names
like any other keyword, so `my_udf(x)` rendered as `MY_UDF(x)` — and `ast_equal`
compared those two strings, saw a difference, and passed the statement through as
"would change semantics". Unquoted SQL function names are case-insensitive, so
there was no semantic change to protect against.

The comma join is a decision REVERSED from an earlier one. It used to decline,
on the grounds that rebuilding it through `join_keyword` emits `FROM a JOIN b`
and Postgres rejects that. True — but emitting the COMMA was the option that was
missed, and it is both valid and the author's own syntax.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- user-defined functions ----------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select my_custom_func(a, b) from t;", "SELECT MY_CUSTOM_FUNC(a, b)"),
    ("select jsonb_array_elements(x) from t;", "SELECT JSONB_ARRAY_ELEMENTS(x)"),
    ("select compute_ltv(cust.id) as ltv from customers cust;",
     "SELECT COMPUTE_LTV(cust.id) AS ltv"),
])
def test_unknown_functions_format(sql, expect):
    assert fmt(sql).startswith(expect)


def test_a_udf_follows_keyword_case():
    """Function names are cased like any other keyword — the documented rule."""
    assert fmt("select MY_UDF(a) from t;", Style(keyword_case="lower")).startswith(
        "select my_udf(a)")


def test_the_net_still_catches_a_real_change():
    """Casefolding the name for comparison must not blind the safety check to
    anything that actually differs."""
    assert not ast_equal("select foo(a) from t;", "select bar(a) from t;", "postgres")
    assert not ast_equal("select foo(a) from t;", "select foo(b) from t;", "postgres")
    assert ast_equal("select foo(a) from t;", "select FOO(a) from t;", "postgres")


@pytest.mark.parametrize("sql", [
    "select my_custom_func(a, b) from t;",
    "select coalesce(my_udf(a), 0) as v from t;",
    "select a from t where my_check(a) = true;",
])
def test_udf_semantics_and_idempotence(sql):
    out = fmt(sql)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out) == out


# ---- comma joins ---------------------------------------------------------

def test_the_comma_form_is_emitted_as_a_comma():
    assert fmt("select 1 from a x, b y where x.id = y.id;") == (
        "SELECT 1\n"
        "FROM a x\n"
        "   , b y\n"
        "WHERE x.id = y.id;"
    )


def test_three_way_comma_join():
    assert fmt("select 1 from a, b, c;") == "SELECT 1\nFROM a\n   , b\n   , c;"


def test_a_comma_join_mixes_with_an_explicit_one():
    out = fmt("select 1 from a x, b y join c z on z.id = x.id;")
    assert "\n   , b y" in out and "\nJOIN c" in out, out


def test_comma_joins_honour_comma_position():
    out = fmt("select 1 from a, b;", Style(comma_position="trailing"))
    assert out == "SELECT 1\nFROM a,\n     b;", out


def test_the_river_puts_the_comma_under_the_table_column():
    out = fmt("select 1 from a, b;", preset_style("river"))
    assert out == "SELECT 1\n  FROM a\n     , b;", out


# ---- LATERAL -------------------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select e.value from t, lateral unnest(t.tags) as e;",
     "FROM t\n   , LATERAL UNNEST(t.tags) e;"),
    ("select 1 from t, lateral jsonb_array_elements(t.data) as e;",
     "FROM t\n   , LATERAL JSONB_ARRAY_ELEMENTS(t.data) e;"),
    ("select 1 from a cross join lateral generate_series(1, 3) as g;",
     "FROM a\nCROSS JOIN LATERAL GENERATE_SERIES(1, 3) g;"),
])
def test_lateral_over_a_set_returning_function(sql, expect):
    assert fmt(sql).endswith(expect)


def test_a_lateral_alias_joins_the_block_alias_column():
    out = fmt("select 1 from customers cust, lateral unnest(cust.tags) t;")
    rows = [ln for ln in out.split("\n") if ln.startswith(("FROM", "   ,"))]
    assert len({ln.rindex(" ") for ln in rows}) == 1, out


@pytest.mark.parametrize("sql", [
    "select e.value from t, lateral unnest(t.tags) as e;",
    "select 1 from a cross join lateral generate_series(1, 3) as g;",
])
def test_lateral_semantics_and_idempotence(sql):
    out = fmt(sql)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out) == out


def test_a_lateral_subquery_gets_the_derived_table_geometry():
    """Its alias lives on the Lateral rather than on the subquery, which is why
    it could not simply fall through to the derived-table path."""
    assert fmt("select 1 from a, lateral (select x from b) c;") == (
        "SELECT 1\n"
        "FROM a\n"
        "   , LATERAL (SELECT x\n"
        "              FROM b\n"
        "             ) c;"
    )


# ---- invariants ----------------------------------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected
