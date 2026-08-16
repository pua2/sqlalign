"""The last pass: three more silent bugs, seven more gaps, and a message that
tells you whose fault a decline is.

A wider sweep — 47 statements of deliberately awkward-but-real SQL — found three
statements coming back with the "would change semantics" wording, which means
the RENDERER is wrong rather than the construct unsupported. Two of the three
were **silent keyword drops**, and both change what the query does:

    select top 10 percent a from t;      -- rendered `TOP 10`, a different query
    create table t as select … with data;  -- `WITH DATA` gone entirely

The third was not sqlalign's at all: sqlglot cannot round-trip `array[1, 2][1]`
through its own generator (the re-parse wraps the array in a `Paren` node), so
`ast_equal` can never pass for that statement no matter what any formatter
emits. That distinction now has its own message and its own decline kind,
because "would change semantics" is the wording the suite sweeps for as a bug
report — see tests/test_no_silent_declines.py — and an upstream fault reported
that way sends you looking in the wrong place.

It also reclassified a decline that was already documented: Redshift `TEXT`.
sqlglot rewrites it to `VARCHAR(MAX)`, so sqlglot cannot round-trip that either,
and the fault was never sqlalign's.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import preset_style


def fmt(sql, dialect="postgres", style=None):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- the two silent keyword drops -----------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select top 10 percent a from t;", "SELECT TOP 10 PERCENT a"),
    ("select top 5 with ties a from t order by a;", "SELECT TOP 5 WITH TIES a"),
    ("select top 10 percent with ties a from t order by a;",
     "SELECT TOP 10 PERCENT WITH TIES a"),
    ("select top 10 a from t;", "SELECT TOP 10 a"),
])
def test_top_modifiers_survive(sql, expect):
    """They live on a `limit_options` node beside the count, and reading only
    the count dropped them: `TOP 10 PERCENT` came out as `TOP 10`, which is a
    different query. Nothing said so — the guard caught the meaning change and
    the statement passed through, so it read as an unsupported construct rather
    than a lost keyword."""
    assert fmt(sql, "tsql").startswith(expect)


def test_dropping_percent_really_would_change_the_query():
    """The premise, asserted directly: this is not a cosmetic difference."""
    assert not ast_equal("select top 10 percent a from t",
                         "select top 10 a from t", "tsql")


@pytest.mark.parametrize("sql,expect", [
    ("create table t as select a from u with data;", "WITH DATA;"),
    ("create table t as select a from u with no data;", "WITH NO DATA;"),
])
def test_with_data_survives(sql, expect):
    """Only `MaterializedProperty` was ever read off a CTAS's `properties`, so
    every other property fell out of the output silently. `WITH NO DATA` creates
    an EMPTY table."""
    assert fmt(sql).endswith(expect)


def test_an_unknown_ctas_property_declines_rather_than_vanishing():
    """The general fix, not just the one property: anything else on that list
    would have gone the same silent way, so an unrecognised property is now a
    named decline."""
    import sqlglot
    from sqlglot import exp

    from sqlalign.config import Width
    from sqlalign.layout import Unsupported
    from sqlalign.layout.ddl import ddl_lines

    node = sqlglot.parse_one("create table t as select a from u", read="postgres")
    node.set("properties", exp.Properties(expressions=[exp.SqlSecurityProperty()]))
    with pytest.raises(Unsupported, match="SqlSecurityProperty"):
        ddl_lines(node, "postgres", Width(100))


# ---- whose fault is this decline ------------------------------------------

def test_an_upstream_round_trip_failure_says_so():
    """sqlglot re-parses its own `(ARRAY[1, 2])[1]` with an extra Paren node, so
    `ast_equal` can never pass here whatever sqlalign emits."""
    result = format_sql("select array[1, 2][1] from t;", "postgres")
    assert result.declines[0].kind == "upstream"
    assert any("cannot round-trip" in w for w in result.warnings)
    assert not any("would change semantics" in w for w in result.warnings)


def test_the_redshift_text_rewrite_is_upstream_too():
    """It was reported as a semantic change for as long as it has been caught.
    The rewrite is sqlglot's — `TEXT` becomes `VARCHAR(MAX)` — so no formatter
    built on it could satisfy the check either."""
    result = format_sql("create table t (a text not null);", "redshift")
    assert result.declines[0].kind == "upstream"


def test_a_real_renderer_fault_still_says_would_change_semantics():
    """The distinction has to cut both ways, or it is just a softer message.

    A statement sqlglot round-trips cleanly, whose output nonetheless differs,
    is sqlalign's fault and must keep the wording the suite sweeps for.
    """
    from sqlalign import formatter

    assert formatter._round_trips("select a from t", "postgres")
    assert formatter._round_trips("select top 10 percent a from t", "tsql")
    assert not formatter._round_trips("select array[1, 2][1] from t", "postgres")


# ---- the seven remaining gaps ---------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select count(*) over () from t;", "SELECT COUNT(*) OVER ()"),
    ("select a from t window w as (), v as (partition by a);",
     "WINDOW w AS ()\n     , v AS (PARTITION BY a);"),
])
def test_empty_window_specs(sql, expect):
    """`OVER ()` means "the whole result set" — ordinary SQL, and there is
    nothing to lay out."""
    out = fmt(sql)
    assert out.startswith(expect) or out.endswith(expect), out


def test_between_symmetric():
    """It means the bounds may be given in either order, so dropping it WOULD
    change the meaning — which is why it declined rather than being ignored."""
    assert fmt("select a from t where a between symmetric 2 and 1;").endswith(
        "WHERE a BETWEEN SYMMETRIC 2 AND 1;")


def test_not_between_symmetric():
    assert fmt("select a from t where a not between symmetric 1 and 2;").endswith(
        "WHERE a NOT BETWEEN SYMMETRIC 1 AND 2;")


def test_group_by_all():
    assert fmt("select a from t group by all;") == "SELECT a\nFROM t\nGROUP BY ALL;"


def test_insert_default_values():
    """There is no body at all, which is why the body check rejected it."""
    assert fmt("insert into t default values;") == "INSERT INTO t\nDEFAULT VALUES;"


def test_insert_default_values_with_returning():
    assert fmt("insert into t default values returning id;").endswith("RETURNING id;")


def test_a_parenthesised_set_operation_arm():
    """The parens are load-bearing: they scope the ORDER BY to the second arm,
    where without them it orders the whole union. The arm declined for exactly
    that reason rather than risk dropping them."""
    assert fmt("select a from t1 union all (select b from t2 order by 1);") == (
        "SELECT a\n"
        "FROM t1\n"
        "\n"
        "UNION ALL\n"
        "\n"
        "(SELECT b\n"
        " FROM t2\n"
        " ORDER BY 1);"
    )


def test_both_arms_parenthesised():
    out = fmt("(select a from t1) union all (select b from t2);")
    assert out.startswith("(SELECT a\n FROM t1)") and out.endswith("(SELECT b\n FROM t2);")


def test_create_table_partition_of():
    """A partition declares no columns of its own — it inherits the parent's —
    so there is no column list to align."""
    assert fmt("create table t partition of p for values in (1);") == (
        "CREATE TABLE t PARTITION OF p FOR VALUES IN (1);")


@pytest.mark.parametrize("sql", [
    "create table t partition of p for values from (1) to (10);",
    "create table t partition of p default;",
])
def test_the_other_partition_bounds(sql):
    assert fmt(sql).startswith("CREATE TABLE t PARTITION OF p ")


# ---- what is left, and why ------------------------------------------------

@pytest.mark.parametrize("sql", [
    "insert into t (a) overriding system value values (1);",
    "comment on schema s is null;",
])
def test_sqlglot_parse_errors(sql):
    import sqlglot

    with pytest.raises(sqlglot.ParseError):
        sqlglot.parse_one(sql, read="postgres")


def test_a_command_fallback():
    import sqlglot

    node = sqlglot.parse_one("create temporary table t (a int) on commit drop",
                             read="postgres")
    assert isinstance(node, sqlglot.exp.Command)


# ---- invariants -----------------------------------------------------------

SHAPES = [
    "select count(*) over () from t;",
    "select a from t where a between symmetric 1 and 2;",
    "select a from t group by all;",
    "insert into t default values;",
    "create table t as select a from u with no data;",
    "create table t partition of p for values in (1);",
    "select a from t1 union all (select b from t2 order by 1);",
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
    out = fmt(sql, style=style)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out, style=style) == out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected
