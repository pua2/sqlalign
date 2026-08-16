"""What a 72-statement sweep of ordinary SQL turned up, after the first pass.

Fifteen of seventy-two declined. Two of the fifteen were not gaps at all:

  - `GROUP BY ROLLUP(a, b), CUBE(c)` came back as `would change semantics`, the
    wording that means the RENDERER is wrong. It was not. `ast_equal` compares
    `repr`, which is stricter than sqlglot's own `==` — deliberately, so a
    token-level difference cannot slip past — but `repr` also reflects the args
    dict's INSERTION order. `{rollup, cube}` and `{cube, rollup}` hold identical
    trees and sqlglot itself calls them equal. A FALSE decline, in the safety
    net rather than in the layout, and it would have fired for any construct
    whose rendering happens to reorder a node's args.

  - T-SQL `OFFSET 10 ROWS FETCH NEXT 5 ROWS ONLY` came back as `internal
    formatter error`, the wording reserved for a crash. `exp.Fetch` lands in
    the `limit` arg exactly as `exp.Limit` does; the T-SQL branch took it for a
    LIMIT and read `limit.expression`, which a Fetch does not have. The
    AttributeError fired one line ABOVE the explicit decline meant to catch it.

The rest were ordinary gaps, and this file is them. Two statements still
decline and neither is sqlalign's: `CREATE MATERIALIZED VIEW … WITH NO DATA`
and a mixed-kind `ALTER TABLE t ADD COLUMN c INT, DROP COLUMN d` are both
`exp.Command` — sqlglot did not parse them, so there is no tree to lay out.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- the two that were bugs ----------------------------------------------

def test_group_by_rollup_and_cube_together():
    """A false decline from the safety net, not a renderer fault.

    sqlglot stores ROLLUP and CUBE in separate args and loses their relative
    order, so which comes out first is sqlalign's choice — the same forced-render
    situation as `<>`/`!=` and DECIMAL/NUMERIC, where the parser destroys the
    distinction.
    """
    sql = "select a from t group by rollup(a, b), cube(c);"
    out = fmt(sql)
    assert ast_equal(sql, out, "postgres")
    assert "ROLLUP (a, b)" in out and "CUBE (c)" in out


def test_arg_order_alone_is_not_a_semantic_difference():
    """The general form of the bug above, pinned directly at `ast_equal`."""
    a = "select a from t group by rollup(a, b), cube(c)"
    b = "select a from t group by cube(c), rollup(a, b)"
    assert ast_equal(a, b, "postgres"), "arg insertion order is not meaning"


def test_the_guard_is_still_strict_about_real_differences():
    """Sorting the args must not have made the comparison lenient."""
    assert not ast_equal("select a from t", "select b from t", "postgres")
    assert not ast_equal("select a from t group by rollup(a)",
                         "select a from t group by cube(a)", "postgres")


def test_tsql_paging_does_not_crash():
    """It crashed one line above the decline that was meant to catch it, so it
    surfaced as `internal formatter error` — the wording that means a bug."""
    result = format_sql(
        "select a from t order by a offset 10 rows fetch next 5 rows only;", "tsql")
    assert result.warnings == []
    assert not any("internal formatter error" in w for w in result.warnings)
    assert result.text.endswith("OFFSET 10\nFETCH NEXT 5 ROWS ONLY;")


# ---- predicates -----------------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select a from t where b similar to 'x%';", "WHERE b SIMILAR TO 'x%';"),
    ("select a from t where b ~ 'x';", "WHERE b ~ 'x';"),
    ("select a from t where b ~* 'x';", "WHERE b ~* 'x';"),
    ("select a from t where b !~ 'x';", "WHERE b !~ 'x';"),
    ("select a from t where b !~* 'x';", "WHERE b !~* 'x';"),
])
def test_pattern_operators(sql, expect):
    """`!~` is its own operator, not a negated one — sqlglot prints the same
    tree as `NOT b ~ 'x'`, but the author wrote an operator and it belongs in
    the operator column."""
    assert fmt(sql).endswith(expect)


@pytest.mark.parametrize("sql,expect", [
    ("select 1 where false;", "SELECT 1\nWHERE FALSE;"),
    ("select a from t where flag;", "WHERE flag;"),
    ("select a from t where not flag;", "WHERE NOT flag;"),
    ("select a from t where not (a and b);", "WHERE NOT (a AND b);"),
    ("select a from t where my_check(a);", "WHERE MY_CHECK(a);"),
])
def test_predicates_with_no_operator(sql, expect):
    """A bare boolean has nothing to tag, so it is one untagged segment that
    simply does not participate in the operator column — the same treatment an
    unaliased table gets in the alias column."""
    assert fmt(sql).endswith(expect)


# ---- DML ------------------------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("insert into t (a) values (1) returning id;", "RETURNING id;"),
    ("delete from t where a = 1 returning *;", "RETURNING *;"),
    ("update t set a = 1 where b = 2 returning a;", "RETURNING a;"),
])
def test_returning(sql, expect):
    assert fmt(sql).endswith(expect)


def test_update_from():
    """Postgres's join-in-an-update, the same shape DELETE's USING has. It sits
    between SET and the WHERE that references it, which is where it is written."""
    assert fmt("update t set a = 1 from u where u.i = t.i;") == (
        "UPDATE t\nSET a = 1\nFROM u\nWHERE u.i = t.i;")


def test_merge_then_delete():
    assert fmt("merge into t using u on t.i = u.i when matched then delete;") == (
        "MERGE INTO t\nUSING u\n  ON t.i = u.i\nWHEN MATCHED\nTHEN DELETE;")


def test_the_merge_action_keyword_is_cased():
    """sqlglot keeps it as a Var carrying the case it was written in, so
    `then delete` would have come out lowercase — and casing it made the output
    compare unequal until `_normalize` casefolded that Var."""
    assert "THEN DELETE" in fmt(
        "MERGE INTO t USING u ON t.i = u.i WHEN MATCHED THEN delete;")


# ---- DDL ------------------------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("drop table if exists t cascade;", "DROP TABLE IF EXISTS t CASCADE;"),
    ("drop index idx;", "DROP INDEX idx;"),
    ("create schema s;", "CREATE SCHEMA s;"),
    ("create schema if not exists s;", "CREATE SCHEMA IF NOT EXISTS s;"),
])
def test_one_line_utility_statements(sql, expect):
    """Nothing in them to model: no list, no column, nothing for the resolver to
    hold. Laying them out by hand would only be a chance to get a keyword wrong."""
    assert fmt(sql) == expect


@pytest.mark.parametrize("sql,expect", [
    ("comment on table t is 'x';", "COMMENT ON TABLE t IS 'x';"),
    ("comment on column t.a is 'y';", "COMMENT ON COLUMN t.a IS 'y';"),
])
def test_comment_on(sql, expect):
    """sqlglot echoes the object kind in whatever case it was WRITTEN in, so
    `comment on table t` rendered `COMMENT ON table t` — one lowercase keyword
    stranded in an uppercased statement."""
    assert fmt(sql) == expect


# ---- alias column lists ---------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select * from generate_series(1, 10) as g(n);", "FROM GENERATE_SERIES(1, 10) g(n);"),
    ("select a from (select 1 as x) d(y);", ") d(y);"),
])
def test_alias_column_lists(sql, expect):
    """`table_alias` rendered only the alias IDENTIFIER, dropping the column
    list, so it had to be guarded against upstream instead."""
    assert fmt(sql).endswith(expect)


def test_values_in_the_from_position():
    assert fmt("select * from (values (1, 2), (3, 4)) as v(a, b);") == (
        "SELECT *\nFROM (VALUES (1, 2), (3, 4)) AS v(a, b);")


def test_values_in_from_keeps_its_parens_beside_a_join():
    """It fell through to the plain table path, which renders the Values node
    WITHOUT its parens — `FROM VALUES (1, 2) v(a, b)`, which Postgres rejects.
    `ast_equal` cannot see it: sqlglot reads its own lenient output back without
    complaint, the same blind spot the legacy comma join sits in.
    """
    out = fmt("select * from (values (1, 2)) as v(a, b) join t on t.i = v.a;")
    assert "FROM (VALUES (1, 2)) AS v(a, b)" in out, out


# ---- what remains, and why it is not sqlalign's ---------------------------

@pytest.mark.parametrize("sql", [
    "create materialized view mv as select a from t with no data;",
    "alter table t add column c int, drop column d;",
])
def test_the_last_two_are_sqlglot_command_fallbacks(sql):
    """`exp.Command` is sqlglot's "I could not parse this" node. There is no
    tree to lay out, and the inner text is a raw string — formatting it would
    mean putting a string comparison back inside the safety net."""
    import sqlglot

    assert isinstance(sqlglot.parse_one(sql, read="postgres"), sqlglot.exp.Command)
    assert format_sql(sql, "postgres").warnings


def test_a_same_kind_multi_action_alter_does_parse():
    """The mixed-kind form is what sqlglot cannot take, not multiple actions."""
    assert fmt("alter table t add column c int, add column d text;").startswith(
        "ALTER TABLE t\n")


# ---- invariants -----------------------------------------------------------

SHAPES = [
    "select a from t group by rollup(a, b), cube(c);",
    "select a from t where b similar to 'x%';",
    "select a from t where b !~* 'x';",
    "select a from t where flag;",
    "insert into t (a) values (1) returning id;",
    "update t set a = 1 from u where u.i = t.i returning t.a;",
    "delete from t where a = 1 returning *;",
    "merge into t using u on t.i = u.i when matched then delete;",
    "drop table if exists t cascade;",
    "create schema s;",
    "comment on table t is 'x';",
    "select * from generate_series(1, 10) as g(n);",
    "select * from (values (1, 2)) as v(a, b);",
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


def test_keyword_case_reaches_the_new_statements():
    lower = Style(keyword_case="lower")
    assert fmt("drop table if exists t;", lower) == "drop table if exists t;"
    assert fmt("comment on table t is 'x';", lower) == "comment on table t is 'x';"
    assert fmt("select a from t where b similar to 'x';", lower).endswith(
        "where b similar to 'x';")


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected
