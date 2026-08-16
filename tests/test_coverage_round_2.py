"""`QUALIFY`, `ON CONFLICT`, the `GROUP BY` extensions, and the named `WINDOW`
clause — four more shapes that passed through untouched.

Each reuses geometry that already existed rather than inventing its own, which is
the point worth testing: `QUALIFY` is a predicate clause like `HAVING`, an upsert's
assignments are `UPDATE`'s `SET`, `ROLLUP`/`CUBE`/`GROUPING SETS` are further terms
of the `GROUP BY` list, and a named window is a comma-stacked clause. Two of these
had a shape that looked like it should reuse something and did not — see
`test_a_window_reference_is_not_a_window_spec`.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- QUALIFY -------------------------------------------------------------

def test_qualify_is_a_predicate_clause_like_having():
    assert fmt("select a, row_number() over (partition by b order by c) as rn "
               "from t qualify rn = 1 and a > 0 order by a;", dialect="redshift") == (
        "SELECT a\n"
        "     , ROW_NUMBER() OVER (PARTITION BY b ORDER BY c) AS rn\n"
        "FROM t\n"
        "QUALIFY rn = 1\n"
        "    AND a  > 0\n"
        "ORDER BY a;"
    )


def test_qualify_sits_between_having_and_order_by():
    out = fmt("select a from t group by a having count(*) > 1 qualify a = 1 order by a;")
    positions = [out.index(k) for k in ("HAVING", "QUALIFY", "ORDER BY")]
    assert positions == sorted(positions), out


# ---- ON CONFLICT ---------------------------------------------------------

def test_do_nothing():
    assert fmt("insert into t (a) values (1) on conflict do nothing;") == (
        "INSERT INTO t\n(  a)\nVALUES (1)\nON CONFLICT DO NOTHING;"
    )


def test_do_update_reuses_the_update_set_geometry():
    """The assignments are the same construct as an UPDATE's, in the same
    statement — the `=` column aligns exactly as it would there."""
    assert fmt("insert into customers (customer_id, email, updated_at) "
               "values (1, 'a@b.c', 'now') "
               "on conflict (customer_id) do update set email = excluded.email, "
               "updated_at = 'now';") == (
        "INSERT INTO customers\n"
        "(  customer_id\n"
        " , email\n"
        " , updated_at)\n"
        "VALUES (1, 'a@b.c', 'now')\n"
        "ON CONFLICT (customer_id) DO UPDATE\n"
        "SET email      = excluded.email\n"
        "  , updated_at = 'now';"
    )


def test_on_constraint_target():
    assert "ON CONFLICT ON CONSTRAINT t_pkey DO NOTHING" in fmt(
        "insert into t (a) values (1) on conflict on constraint t_pkey do nothing;")


def test_conflict_where_clause():
    out = fmt("insert into t (a, b) values (1, 2) on conflict (a) "
              "do update set b = excluded.b where t.c > 0;")
    assert out.endswith("SET b = excluded.b\nWHERE t.c > 0;"), out


# ---- GROUP BY extensions -------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select a from t group by rollup (a, b);", "GROUP BY ROLLUP (a, b);"),
    ("select a from t group by cube (a, b);", "GROUP BY CUBE (a, b);"),
    ("select a, b from t group by grouping sets ((a), (b), ());",
     "GROUP BY GROUPING SETS ((a), (b), ());"),
])
def test_extensions_are_terms_of_the_group_by_list(sql, expect):
    assert fmt(sql).endswith(expect)


def test_an_extension_stacks_with_plain_terms():
    assert fmt("select a, b from t group by a, rollup (b);").endswith(
        "GROUP BY a\n       , ROLLUP (b);")


def test_the_emitted_order_matches_sqlglots():
    """Reordering the clause would be a rewrite even where it is semantically
    inert, so the terms come out expressions-then-grouping_sets-then-cube-then-
    rollup, which is the order sqlglot's own generator uses."""
    import sqlglot
    sql = "select a from t group by a, grouping sets ((b)), rollup (c)"

    def terms(text):
        # Compare with the comma detached: house style leads with it, sqlglot
        # trails it, and that difference is not what this test is about.
        return text.replace(",", " , ").split()

    assert terms(fmt(sql + ";")) == terms(
        sqlglot.parse_one(sql, read="postgres").sql("postgres") + ";")


# ---- named WINDOW --------------------------------------------------------

def test_the_window_clause():
    assert fmt("select a, sum(x) over w from t window w as (partition by b order by c);") == (
        "SELECT a\n"
        "     , SUM(x) OVER w\n"
        "FROM t\n"
        "WINDOW w AS (PARTITION BY b ORDER BY c);"
    )


def test_several_named_windows_stack():
    assert fmt("select sum(x) over w, avg(y) over w2 from t "
               "window w as (partition by b), w2 as (order by c);").endswith(
        "WINDOW w AS (PARTITION BY b)\n     , w2 AS (ORDER BY c);")


def test_a_window_reference_is_not_a_window_spec():
    """`SUM(x) OVER w` is an exp.Window whose name lives in `alias` and which
    carries no spec at all — the spec is in the WINDOW clause. It used to be
    declined as a 'named-window alias', which is why the clause could not work
    even once the clause itself was laid out."""
    assert "SUM(x) OVER w" in fmt(
        "select sum(x) over w from t window w as (partition by b);")


def test_a_frame_survives_into_the_clause():
    assert fmt("select sum(x) over w as total from t "
               "window w as (partition by b rows between unbounded preceding "
               "and current row);").endswith(
        "WINDOW w AS (PARTITION BY b ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW);")


# ---- invariants ----------------------------------------------------------

SHAPES = [
    "select a, row_number() over (partition by b) as rn from t qualify rn = 1;",
    "insert into t (a) values (1) on conflict do nothing;",
    "insert into t (a, b) values (1, 2) on conflict (a) do update set b = excluded.b;",
    "select a from t group by rollup (a, b);",
    "select a, b from t group by grouping sets ((a), (b));",
    "select a, sum(x) over w from t window w as (partition by b);",
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


def test_qualify_composes_with_trailing_booleans():
    out = fmt("select a from t qualify a = 1 and b = 2;",
              Style(boolean_operator_position="trailing"))
    assert "QUALIFY a = 1 AND\n        b = 2;" in out, out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


# ---- what is still declined ---------------------------------------------

@pytest.mark.parametrize("sql", [
    "select * from t pivot (sum(x) for y in (1, 2)) p;",
    "select * from t unpivot (x for y in (a, b)) u;",
])
def test_still_declined(sql):
    result = format_sql(sql, "postgres")
    assert result.warnings
    assert result.text.strip() == sql.strip()
