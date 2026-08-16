"""`SELECT DISTINCT`, `SELECT DISTINCT ON (...)`, and `COUNT(DISTINCT x)`.

All three used to pass through untouched. `exp.Distinct` sat in select.py's
`_UNSUPPORTED_NODES`, which gated it at *every* position — so an aggregate's
`DISTINCT` was declined along with the select-level one, and a file containing
`SELECT DISTINCT` simply did not change.

DISTINCT rides the SELECT head rather than taking a line of its own, which is
where T-SQL's `TOP` already goes. The continuation commas do NOT move to follow
it — the comma column is a fixed house constant, not derived from wherever the
first item happens to start. That was settled when `TOP` landed; this reuses the
ruling rather than inventing a second one:

    SELECT DISTINCT ON (cust.id) cust.id
         , cust.email
    FROM customers cust
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- the three shapes ----------------------------------------------------

def test_plain_distinct():
    assert fmt("select distinct a, bb, ccc from t;") == (
        "SELECT DISTINCT a\n"
        "     , bb\n"
        "     , ccc\n"
        "FROM t;"
    )


def test_distinct_on():
    assert fmt("select distinct on (cust.id) cust.id, cust.email from customers cust;") == (
        "SELECT DISTINCT ON (cust.id) cust.id\n"
        "     , cust.email\n"
        "FROM customers cust;"
    )


def test_distinct_on_multiple_keys():
    assert "SELECT DISTINCT ON (a, b) a" in fmt("select distinct on (a, b) a, b from t;")


def test_distinct_inside_an_aggregate():
    """Not a select-level DISTINCT at all — it was collateral damage from the
    node being gated everywhere rather than at the position that needed it."""
    assert fmt("select count(distinct x) as n from t;") == "SELECT COUNT(DISTINCT x) AS n\nFROM t;"


def test_distinct_aggregate_in_having():
    out = fmt("select a from t group by a having count(distinct b) > 1;")
    assert "HAVING COUNT(DISTINCT b) > 1" in out


def test_distinct_with_tsql_top():
    """DISTINCT precedes TOP, and both ride the same head."""
    assert fmt("select distinct top 10 id, name from users;", dialect="tsql") == (
        "SELECT DISTINCT TOP 10 id\n"
        "     , name\n"
        "FROM users;"
    )


# ---- the comma column does not chase the head ----------------------------

def test_continuation_commas_keep_the_house_column():
    """The ruling TOP established: the comma column is a house constant. A wide
    DISTINCT ON head does not drag it rightward."""
    out = fmt("select distinct on (a_very_long_key_name) a, bb from t;")
    assert "\n     , bb" in out, out


# ---- composition ---------------------------------------------------------

def test_composes_with_select_on_its_own_line():
    assert fmt("select distinct a, b from t;",
               Style(select_placement="own_line", comma_position="trailing")) == (
        "SELECT DISTINCT\n"
        "  a,\n"
        "  b\n"
        "FROM t;"
    )


def test_composes_with_the_river():
    out = fmt("select distinct a, b from t where a > 0;", preset_style("river"))
    assert out.startswith("SELECT DISTINCT a\n"), out
    assert "\n  FROM t" in out


def test_composes_with_lowercase_keywords():
    out = fmt("select distinct a from t;", Style(keyword_case="lower"))
    assert out.startswith("select distinct a")


# ---- invariants ----------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "select distinct a, b from t;",
    "select distinct on (a) a, b from t;",
    "select count(distinct x) from t;",
])
def test_semantics_and_idempotence(sql):
    out = fmt(sql)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out) == out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


# ---- still declined, deliberately ----------------------------------------

def test_distinct_with_an_unmodelled_arg_declines():
    """`exp.Distinct` also carries an `expressions` arg in sqlglot's schema.
    Nothing in the surveyed dialects produces it here, so it declines rather
    than being silently dropped from the output."""
    from sqlglot import exp
    assert set(exp.Distinct.arg_types) == {"expressions", "on"}, (
        "sqlglot's Distinct grew an arg; check _distinct_text still covers it")
