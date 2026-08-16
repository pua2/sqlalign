"""The axes the construct sweeps never varied: style, and dialect.

Every sweep in this suite ran constructs against ONE style (`house`) and mostly
one dialect (`postgres`). That is a real blind spot, and it cost a bug: trailing
punctuation landing inside a trailing comment was invisible to all of them,
because house puts commas and booleans at the START of a row where nothing
follows them. No amount of construct-sweeping reaches it — the axis that
mattered was the style, not the SQL.

So this file varies the other two axes instead, over a corpus that is
deliberately ordinary:

  - **one knob at a time**, every non-default value of every `Style` field
  - **pairs** of the knobs that change ROW SHAPE, where the collisions live
  - **each dialect** against every preset

It asserts only the four invariants that hold regardless of style: it formats,
it means the same thing, it is idempotent, and a decline is never the `safety`
kind — that wording means the renderer is wrong (see
tests/test_no_silent_declines.py).
"""
import dataclasses
import itertools

import pytest

from sqlalign.config import Width
from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import ALL_ALIGN_TARGETS, HOUSE, PRESETS, preset_style

# Non-default values, one list per knob.
KNOBS = {
    "align": [False],
    "align_targets": [frozenset(), ALL_ALIGN_TARGETS, frozenset({"operators"})],
    "neq_style": ["<>"],
    "decimal_style": ["DECIMAL"],
    "table_alias_style": ["as"],
    "comma_position": ["trailing"],
    "boolean_operator_position": ["trailing"],
    "on_placement": ["own_line"],
    "select_placement": ["own_line"],
    "clause_keyword_align": ["river"],
    "river_gutter": [4, 10],
    "select_indent": [1, 4],
    "body_blank_lines": [0, 2],
    "keyword_case": ["lower"],
    "blank_lines_between_statements": [0, 2],
    "width": [Width(40), Width(200), Width(0)],
    "format_dollar_bodies": [False],
    "protect_templating": [False],
}

# The knobs that move things BETWEEN rows -- the ones that can collide.
SHAPE = {
    "comma_position": ["leading", "trailing"],
    "boolean_operator_position": ["leading", "trailing"],
    "on_placement": ["inline", "own_line"],
    "select_placement": ["inline", "own_line"],
    "clause_keyword_align": ["left", "river"],
    "align": [True, False],
    "keyword_case": ["upper", "lower"],
    "width": [Width(100), Width(40)],
}

CORPUS = [
    "select a, b from t inner join u on u.i = t.i and u.k = t.k "
    "where c = 1 and d = 2 order by a, b;",
    "select a -- s\n, b from t -- f\n where c = 1 -- w\n  and d = 2 "
    "group by a -- g\n, b order by a -- o\n, b;",
    "select case x when 1 then 2 else 3 end as s from t "
    "where a not like 'x%' and b is distinct from c;",
    "select a from (select x as a from u) d join w on w.a = d.a "
    "where d.a in (select z from v);",
    "select sum(x) over (partition by a order by b) as r, count(*) over () as n "
    "from t group by a;",
    "update t set a = 1 from u where u.i = t.i returning a;",
    "with c as (select 1 as x) select * from c union all select 2;",
    "create table t (a int primary key, b text not null, c numeric(10, 2));",
    "create function f() returns int as $$ begin return 1; end; $$ language plpgsql;",
]


def _holds(sql, dialect, style, label):
    """The four invariants that hold whatever the style is."""
    result = format_sql(sql, dialect, style)
    if result.warnings:
        decline = result.declines[0]
        assert decline.kind != "safety", (
            f"{label}: renderer changed meaning -- {decline.reason} -- on {sql!r}")
        assert "internal" not in decline.reason, f"{label}: crashed on {sql!r}"
        return                                    # a named or upstream decline is fine
    assert ast_equal(sql, result.text, dialect), f"{label}: meaning changed on {sql!r}"
    assert format_sql(result.text, dialect, style).text == result.text, (
        f"{label}: not idempotent on {sql!r}")


# ---- one knob at a time ---------------------------------------------------

@pytest.mark.parametrize("knob", sorted(KNOBS))
def test_every_knob_value(knob):
    for value in KNOBS[knob]:
        style = dataclasses.replace(HOUSE, **{knob: value})
        for sql in CORPUS:
            _holds(sql, "postgres", style, f"{knob}={value!r}")


# ---- pairs of the shape knobs ---------------------------------------------

@pytest.mark.parametrize("a,b", sorted(itertools.combinations(sorted(SHAPE), 2)))
def test_shape_knob_pairs(a, b):
    """Single-knob variation cannot reach a collision between two of them."""
    for va, vb in itertools.product(SHAPE[a], SHAPE[b]):
        style = dataclasses.replace(HOUSE, **{a: va, b: vb})
        for sql in CORPUS:
            _holds(sql, "postgres", style, f"{a}={va!r}+{b}={vb!r}")


def test_the_pair_that_found_the_bug():
    """Pinned on its own, because a parametrised row is easy to delete: trailing
    commas or trailing booleans, against a statement carrying comments."""
    sql = "select a -- s\n, b from t where c = 1 -- w\n  and d = 2;"
    for knob in ("comma_position", "boolean_operator_position"):
        style = dataclasses.replace(HOUSE, **{knob: "trailing"})
        out = format_sql(sql, "postgres", style)
        assert out.warnings == []
        assert "-- s," not in out.text and "-- w AND" not in out.text


# ---- the dialect axis -----------------------------------------------------

DIALECT_CORPUS = {
    "redshift": [
        "select a from t where b not like 'x%' and c not in (1, 2);",
        "select case status when 'a' then 1 else 0 end as s from t;",
        "select a from (select x as a from u) d join w on w.a = d.a;",
        "select * from (values (1, 2), (3, 4)) as v(a, b);",
        "select a from t group by rollup(a, b), cube(c);",
        "select * from t pivot (sum(x) for y in (1, 2)) p;",
        "select a from t qualify row_number() over (order by a) = 1;",
        "create table t (a bigint not null encode az64, b varchar(20) encode lzo) "
        "distkey(a) sortkey(b);",
        "copy t from 's3://b/k' iam_role 'x';",
        "select a -- c\n, b from t where c = 1 -- w\n  and d = 2;",
    ],
    "tsql": [
        "select top 10 percent a from t;",
        "select top 5 with ties a from t order by a;",
        "select a from t order by a offset 10 rows fetch next 5 rows only;",
        "select a from t where b not like 'x%' and c not in (1, 2);",
        "select * from t pivot (sum(c) for b in ([x], [y])) p;",
        "insert into t (a) output inserted.a select a from src;",
        "update t set a = 1 output inserted.a where b = 2;",
        "declare @a int = 1, @b varchar(10);",
        "create procedure p @x int as begin select @x; end;",
        "select a -- c\n, b from t where c = 1 -- w\n  and d = 2;",
    ],
}


@pytest.mark.parametrize("dialect", sorted(DIALECT_CORPUS))
@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_each_dialect_against_each_preset(dialect, preset):
    style = preset_style(preset)
    for sql in DIALECT_CORPUS[dialect]:
        _holds(sql, dialect, style, f"{dialect}/{preset}")


# ---- dialect rewrites that MUST keep declining ----------------------------

@pytest.mark.parametrize("dialect,sql,note", [
    ("redshift", "select a from t for update;", "Redshift has no locking reads"),
    ("redshift", "alter table t add column b text;", "TEXT becomes VARCHAR(MAX)"),
    ("tsql", "select coalesce(case when x then 1 end, 0) as v from t;",
     "T-SQL has no boolean, so sqlglot inserts `<> 0`"),
])
def test_a_dialect_rewrite_is_reported_as_upstream(dialect, sql, note):
    """sqlglot's dialect generators REWRITE these, so no formatter built on it
    could satisfy `ast_equal`. The `FOR UPDATE` one is the sharpest: Redshift
    drops the clause entirely, and without the guard sqlalign would emit a query
    with its locking silently removed.
    """
    result = format_sql(sql, dialect)
    assert result.warnings, note
    assert result.declines[0].kind == "upstream", (
        f"{note}: reported as {result.declines[0].kind}, which blames sqlalign")
    assert result.text.strip() == sql.strip()
