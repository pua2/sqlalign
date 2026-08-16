"""CASE, window functions and subqueries wherever they appear — not only at the
one position each was modelled for.

Four gaps closed together, because they were all one guard. `_UNSUPPORTED_NODES`
listed `Case`, `Window`, `Subquery` and `Select`, and their presence ANYWHERE in
a statement forced a passthrough; Case and Window were exempted at their
select-list root only. So this formatted:

    select case when x then 1 else 2 end from t;      -- root: modelled

and this did not, though it is the same expression one level down:

    select coalesce(case when x then 1 else 2 end, 0) from t;

The guard was written when nothing rendered those nodes. Something does now:
`render_expr` puts them inline, through the same house generator that cases every
other expression. Inline is also the only sane answer for a nested one — the
multi-line geometry belongs to a select item that OWNS its row, and a CASE buried
in a function argument has no row of its own.

Two more, alongside:

  - the SIMPLE form `CASE x WHEN 1 THEN …` was declined even at the select-list
    root, where the searched form has always laid out. One rule now covers both:
    the first WHEN rides the CASE line and every later WHEN aligns under it,
    which for a searched CASE is the pre-existing `item_col + len("CASE ")`.
  - `FROM (SELECT …) d` was `from: non-table`, though the identical construct in
    JOIN position had always laid out. It now shares that geometry.

What did NOT change, deliberately: the over-width CASE BREAK stays restricted to
the golden-proven `COALESCE(SUM(CASE …))` wrapper. Its END=CASE+1 offset is
invisible to `ast_equal` and only a golden can catch it, so widening it would
ship invented bytes. Every other wrapper renders flat — which invents nothing,
and is what the house already does with any expression it has no modelled break
for.
"""
import itertools

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- the simple CASE form -------------------------------------------------

def test_the_simple_form_lays_out():
    assert fmt("select case status when 'a' then 1 when 'bbbb' then 2 else 0 end "
               "as rank from t;") == (
        "SELECT CASE status WHEN 'a'    THEN 1\n"
        "                   WHEN 'bbbb' THEN 2\n"
        "                   ELSE 0\n"
        "       END AS rank\n"
        "FROM t;"
    )


def test_later_whens_align_under_the_first():
    """The one rule that covers both forms. The operand rides the CASE line, so
    the WHEN column moves right with it."""
    out = fmt("select case status when 'a' then 1 when 'b' then 2 end from t;")
    whens = [ln.index("WHEN") for ln in out.split("\n") if "WHEN" in ln]
    assert len(whens) == 2 and len(set(whens)) == 1, out


def test_the_searched_form_is_unchanged():
    assert fmt("select case when x then 1 else 2 end from t;") == (
        "SELECT CASE WHEN x THEN 1\n"
        "            ELSE 2\n"
        "       END\n"
        "FROM t;"
    )


def test_end_sits_under_case_in_both_forms():
    for sql in ("select case when x then 1 end from t;",
                "select case y when 1 then 2 end from t;"):
        out = fmt(sql)
        lines = out.split("\n")
        assert lines[0].index("CASE") == lines[-2].index("END"), out


# ---- nested anywhere ------------------------------------------------------

NESTED = [
    "case when x > 0 then 1 else 2 end",
    "case status when 'a' then 1 when 'b' then 2 else 0 end",
    "row_number() over (partition by a order by b)",
    "sum(y) over (order by b rows between unbounded preceding and current row)",
    "(select max(z) from u)",
    "coalesce(case when x then 1 end, 0)",
    "my_udf(case when x then 1 else 2 end)",
]

POSITIONS = [
    "select {e} as v from t;",
    "select coalesce({e}, 0) as v from t;",
    "select a from t where {e} = 1;",
    "select a from t where x = 1 and {e} = 2;",
    "select a from t group by {e};",
    "select a from t group by a having {e} = 1;",
    "select a from t order by {e};",
    "select a from t join u on u.k = {e};",
    "with c as (select {e} as v from t) select * from c;",
    "update t set a = {e};",
]


@pytest.mark.parametrize("position", POSITIONS)
def test_every_position_formats_and_means_the_same(position):
    for expr in NESTED:
        sql = position.format(e=expr)
        out = fmt(sql)
        assert ast_equal(sql, out, "postgres"), sql
        assert fmt(out) == out, f"not idempotent: {sql}"


@pytest.mark.parametrize("sql,expect", [
    ("select coalesce(case when x then 1 else 2 end, 0) as v from t;",
     "SELECT COALESCE(CASE WHEN x THEN 1 ELSE 2 END, 0) AS v"),
    ("select coalesce(row_number() over (order by a), 0) as r from t;",
     "SELECT COALESCE(ROW_NUMBER() OVER (ORDER BY a), 0) AS r"),
    ("select a from t where b = (select max(x) from u);",
     "WHERE b = (SELECT MAX(x) FROM u);"),
])
def test_a_nested_construct_renders_inline(sql, expect):
    """Inline, not multi-line: the geometry belongs to a select item that owns
    its row, and a CASE inside a function argument has none."""
    assert expect in fmt(sql)


def test_a_root_case_still_gets_its_own_rows():
    """Allowing the nested form must not have flattened the modelled one."""
    assert fmt("select case when x then 1 else 2 end from t;").count("\n") == 3


# ---- a derived table in the FROM position ---------------------------------

def test_derived_table_in_from():
    assert fmt("select a from (select x as a from u) d;") == (
        "SELECT a\n"
        "FROM (SELECT x AS a\n"
        "      FROM u\n"
        "     ) d;"
    )


def test_it_matches_the_join_position_geometry():
    """The same construct laid out identically in both positions — it used to be
    `from: non-table` in one and modelled in the other.

    `FROM` and `JOIN` are both four characters, so the derived table's own block
    is byte-for-byte the same string; that is the whole assertion.
    """
    block = "(SELECT x AS a\n      FROM u\n     ) d"
    assert fmt("select a from (select x as a from u) d;") == f"SELECT a\nFROM {block};"
    assert fmt("select a from w join (select x as a from u) d on d.a = w.a;") == (
        f"SELECT a\nFROM w\nJOIN {block} ON d.a = w.a;")


def test_a_derived_from_keeps_its_shape_beside_a_join():
    """It fell through to the flat render when the statement also had a JOIN —
    the same statement laid out two ways depending on whether a JOIN was there."""
    out = fmt("select a from (select x as a from u) d join v on v.a = d.a;")
    assert "FROM (SELECT x AS a\n      FROM u\n     ) d\nJOIN v ON v.a = d.a;" in out


def test_a_derived_from_with_a_comma_join():
    out = fmt("select a from (select x as a from u) d, w;")
    assert out.endswith("     ) d\n   , w;"), out


def test_an_unaliased_derived_table_still_declines():
    """Postgres requires the alias; declining is better than inventing one."""
    result = format_sql("select a from (select x as a from u);", "postgres")
    assert result.warnings


# ---- invariants -----------------------------------------------------------

SHAPES = [
    "select case status when 'a' then 1 else 0 end as s from t;",
    "select coalesce(case when x then 1 else 2 end, 0) as v from t;",
    "select a from t where case x when 1 then 2 else 3 end = 2;",
    "select coalesce(row_number() over (order by a), 0) as r from t;",
    "select a from t where b = (select max(x) from u);",
    "select a from (select x as a from u) d;",
    "select a from (select x as a from u) d join v on v.a = d.a;",
]


@pytest.mark.parametrize("sql", SHAPES)
def test_semantics_and_idempotence(sql):
    out = fmt(sql)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out) == out


@pytest.mark.parametrize("sql", SHAPES)
@pytest.mark.parametrize("preset", ["compact", "gitlab", "river", "dbt", "trailing"])
def test_they_compose_with_the_presets(sql, preset):
    style = preset_style(preset)
    out = fmt(sql, style)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out, style) == out


def test_keyword_case_reaches_a_nested_construct():
    out = fmt("select coalesce(case when x then 1 end, 0) as v from t;",
              Style(keyword_case="lower"))
    assert "coalesce(case when x then 1 end, 0)" in out, out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


def test_no_silent_declines_in_the_new_coverage():
    """A `would change semantics` decline anywhere in here is a renderer bug,
    not a construct decision — see tests/test_no_silent_declines.py."""
    offenders = []
    for position, expr in itertools.product(POSITIONS, NESTED):
        sql = position.format(e=expr)
        result = format_sql(sql, "postgres")
        if any("would change semantics" in w for w in result.warnings):
            offenders.append(sql)
    assert not offenders, offenders
