"""The tail of the decline list, cleared.

A sweep of 64 ordinary Postgres statements left eight declines. Six were gaps
rather than decisions, and this is them. The other two are not sqlalign's to
close: `ORDER BY a USING <` is a **sqlglot ParseError**, and `EXPLAIN …` falls
back to `exp.Command` whose inner query is a raw STRING rather than a parsed
tree — there is no structure there to format, and re-parsing that string to fake
one would put a string comparison back inside the safety net, which is precisely
how the user-defined-function bug worked.

One of the six was not a gap at all but a BUG, and it had a test pinning it:

    select a from t where x not like '%z%';

`exp.Like` carries negation as a FLAG rather than as a wrapping `exp.Not`. The
flag was never read, so the renderer emitted plain `LIKE`, the re-parse guard
caught the meaning change, and the statement passed through — and
`test_robustness.py` listed it as an EXPECTED "formatting would change
semantics" decline. A decline with that wording is never expected; it means the
renderer is wrong. See tests/test_no_silent_declines.py, whose predicate sweep
was added because it had missed this one.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- negation -------------------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select a from t where x not like '%z%';", "WHERE x NOT LIKE '%z%';"),
    ("select a from t where x not ilike '%z%';", "WHERE x NOT ILIKE '%z%';"),
    ("select a from t where x not in (1, 2);", "WHERE x NOT IN (1, 2);"),
    ("select a from t where x not between 1 and 10;", "WHERE x NOT BETWEEN 1 AND 10;"),
    ("select a from t where x is not null;", "WHERE x IS NOT NULL;"),
])
def test_negated_predicates(sql, expect):
    assert fmt(sql).endswith(expect)


def test_not_like_was_dropping_the_negation():
    """The bug this file's docstring is about — pinned as its own case because a
    parametrised row is easy to delete."""
    sql = "select a from t where x not like '%z%';"
    out = fmt(sql)
    assert "NOT LIKE" in out
    assert ast_equal(sql, out, "postgres")
    assert not ast_equal(sql, out.replace("NOT LIKE", "LIKE"), "postgres"), (
        "the guard would not have caught this; the premise of the fix is wrong")


def test_the_negation_stays_in_the_operator_column():
    """NOT rides the operator, which means it joins the house's right-aligned
    `op` column instead of pushing the left-hand side out of its own:

        WHERE x        = 1
          AND y NOT LIKE 'a%'
          AND z   NOT IN (1, 2);

    A three-character operator and a nine-character one end in the same column,
    so every right-hand side starts in the same column. That is the assertion —
    it fails if NOT is emitted anywhere but the operator segment.
    """
    out = fmt("select a from t where x = 1 and y not like 'a%' and z not in (1, 2);")
    assert out == (
        "SELECT a\n"
        "FROM t\n"
        "WHERE x        = 1\n"
        "  AND y NOT LIKE 'a%'\n"
        "  AND z   NOT IN (1, 2);"
    )


@pytest.mark.parametrize("sql,expect", [
    ("select a from t where not exists (select 1 from u);",
     "WHERE NOT EXISTS (SELECT 1\n                  FROM u);"),
    ("select a from t where x not in (select y from u);",
     "WHERE x NOT IN (SELECT y\n                FROM u);"),
])
def test_negated_subquery_predicates(sql, expect):
    """`x NOT IN (...)`, never `NOT x IN (...)` — both are valid and mean the
    same thing, but only one is how anyone writes it.

    The negation also HAS to ride the keyword rather than the left-hand side:
    the subquery body's line indents are baked in from the keyword's own width,
    so `NOT` anywhere else leaves the body detached from its own paren. That is
    what the second line of each expectation pins — the body's `FROM` sits one
    column past the `(`.
    """
    assert fmt(sql).endswith(expect)


# ---- operators that were simply missing -----------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select a from t where b is distinct from c;", "WHERE b IS DISTINCT FROM c;"),
    ("select a from t where b is not distinct from c;", "WHERE b IS NOT DISTINCT FROM c;"),
])
def test_the_null_safe_comparisons(sql, expect):
    """The standard way to compare nullable columns. They declined only because
    they were missing from the operator table."""
    assert fmt(sql).endswith(expect)


def test_like_with_an_escape():
    """`ESCAPE` qualifies the pattern, so it rides the right-hand segment. In the
    operator column it would align `ESCAPE` under a bare `=` on the row above."""
    assert fmt("select a from t where b like 'x%' escape '!';").endswith(
        "WHERE b LIKE 'x%' ESCAPE '!';")


# ---- clauses that were simply missing -------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("select a from t for update;", "FOR UPDATE;"),
    ("select a from t for update skip locked;", "FOR UPDATE SKIP LOCKED;"),
    ("select a from t for share nowait;", "FOR SHARE NOWAIT;"),
    ("select a from t for update of t;", "FOR UPDATE OF t;"),
    ("select a from t for no key update;", "FOR NO KEY UPDATE;"),
])
def test_row_level_locks(sql, expect):
    """Each lock is rendered whole by sqlglot rather than rebuilt from its
    update/wait flags — those spell four keyword pairs and getting one backwards
    would change the locking behaviour with nothing to catch it."""
    assert fmt(sql).endswith(expect)


def test_a_lock_closes_the_statement():
    assert fmt("select a from t order by a limit 5 for update skip locked;") == (
        "SELECT a\nFROM t\nORDER BY a\nLIMIT 5\nFOR UPDATE SKIP LOCKED;")


@pytest.mark.parametrize("sql,expect", [
    ("with x as materialized (select 1 as a) select * from x;", "WITH x AS MATERIALIZED ("),
    ("with x as not materialized (select 1 as a) select * from x;",
     "WITH x AS NOT MATERIALIZED ("),
    ("with x as (select 1 as a) select * from x;", "WITH x AS ("),
])
def test_the_cte_materialization_hint(sql, expect):
    """It changes PLANNING, so dropping it would silently change how the query
    runs — which is why it declined rather than being ignored."""
    assert fmt(sql).startswith(expect)


def test_delete_using():
    assert fmt("delete from t using u where t.i = u.i;") == (
        "DELETE FROM t\nUSING u\nWHERE t.i = u.i;")


def test_delete_using_several_tables():
    assert fmt("delete from t using u, v where t.i = u.i;").startswith(
        "DELETE FROM t\nUSING u, v\n")


# ---- ALTER ----------------------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ("alter table t add column c int;", "ALTER TABLE t ADD COLUMN c INT;"),
    ("alter table t drop column c;", "ALTER TABLE t DROP COLUMN c;"),
    ("alter table t rename to u;", "ALTER TABLE t RENAME TO u;"),
    ("alter table t alter column c set not null;",
     "ALTER TABLE t ALTER COLUMN c SET NOT NULL;"),
    ("alter table t add constraint pk primary key (a);",
     "ALTER TABLE t ADD CONSTRAINT pk PRIMARY KEY (a);"),
    ("alter view v rename to w;", "ALTER VIEW v RENAME TO w;"),
    ("alter table if exists t add column c int;",
     "ALTER TABLE IF EXISTS t ADD COLUMN c INT;"),
    ("alter table only t add column c int;", "ALTER TABLE ONLY t ADD COLUMN c INT;"),
])
def test_alter_one_action_rides_the_head(sql, expect):
    assert fmt(sql) == expect


def test_alter_several_actions_stack():
    """Two or more actions are a list, and a list gets the house's leading-comma
    column. One action is not a list, which is the whole reason for the split."""
    assert fmt("alter table t add column a int, add column b text;") == (
        "ALTER TABLE t\n"
        "    ADD COLUMN a INT\n"
        "  , ADD COLUMN b TEXT;"
    )


def test_an_unparsed_alter_still_declines():
    """`ALTER TABLE t OWNER TO bob` is an `exp.Command` — sqlglot did not parse
    the action, so there is nothing to lay out."""
    result = format_sql("alter table t owner to bob;", "postgres")
    assert result.warnings
    assert result.text.strip() == "alter table t owner to bob;"


# ---- what is NOT sqlalign's to fix ----------------------------------------

def test_explain_declines_because_sqlglot_does_not_parse_it():
    """Its inner query is a raw STRING on an `exp.Command`, not a tree. Faking a
    tree by re-parsing that string would put a string comparison back inside
    `ast_equal` — exactly the shape of the user-defined-function bug."""
    import sqlglot

    node = sqlglot.parse_one("explain select a from t", read="postgres")
    assert isinstance(node, sqlglot.exp.Command), "sqlglot parses EXPLAIN now; revisit"
    assert isinstance(node.args["expression"], sqlglot.exp.Literal)
    assert format_sql("explain select a from t;", "postgres").warnings


def test_order_by_using_is_a_sqlglot_parse_error():
    import sqlglot

    with pytest.raises(sqlglot.ParseError):
        sqlglot.parse_one("select a from t order by a using <", read="postgres")


# ---- invariants -----------------------------------------------------------

SHAPES = [
    "select a from t where x not like '%z%';",
    "select a from t where x not in (1, 2);",
    "select a from t where x not between 1 and 10;",
    "select a from t where not exists (select 1 from u);",
    "select a from t where b is distinct from c;",
    "select a from t where b like 'x%' escape '!';",
    "select a from t for update skip locked;",
    "with x as materialized (select 1 as a) select * from x;",
    "delete from t using u where t.i = u.i;",
    "alter table t add column c int;",
    "alter table t add column a int, add column b text;",
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


def test_keyword_case_reaches_the_new_keywords():
    lower = Style(keyword_case="lower")
    assert fmt("select a from t for update skip locked;", lower).endswith(
        "for update skip locked;")
    assert fmt("select a from t where x not like 'a';", lower).endswith("not like 'a';")
    assert fmt("alter table t add column c int;", lower) == "alter table t add column c int;"


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected
