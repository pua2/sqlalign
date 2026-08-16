"""Comments in WHERE / HAVING / QUALIFY.

The comment engine modelled the select list and declined everything else, which
meant a single `-- why` in a WHERE clause passed the WHOLE statement through —
and a comment in a WHERE clause is about as common as SQL gets.

The select list was modellable because its source geometry lines up with its
layout: depth-0 commas partition it into one slot per item, and the layout puts
one ROW per item, so a comment lands on the row the author wrote it against. The
predicate clauses have exactly the same property one separator over — depth-0
`AND`/`OR` boundaries, one row each from `condition_block` — so the same
machinery covers them once the splitter is told which separator to use.

`FROM`, `GROUP BY` and `ORDER BY` still decline. GROUP BY and ORDER BY are
comma-separated like the select list, but `comma_clause` lays them out from
rendered TEXT rather than from nodes, so there is nothing to hang the annotation
on; FROM is table references and joins, which is a different shape again.

Two pre-existing bugs surfaced on the way, both about what happens at the END of
a statement:

  - the statement terminator was appended AFTER a trailing line comment, so
    `select a -- note` came out `SELECT a -- note;` with the `;` INSIDE the
    comment. The statement is then unterminated, and in a multi-statement file
    that swallows whatever follows.
  - fixing that moved the comment past the `;`, which exposed the inter-statement
    joiner assuming the whole gap lives in the FOLLOWING statement's prefix. A
    statement ending in a trailing comment ends with its own newline, so the gap
    grew by one blank line on every pass.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql


def fmt(sql, dialect="postgres"):
    result = format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


def _fixed_point(sql, dialect="postgres"):
    outs, text = [], sql
    for _ in range(3):
        text = fmt(text, dialect)
        outs.append(text)
    assert len(set(outs)) == 1, f"not idempotent: {outs}"
    return outs[0]


# ---- the clause that mattered ---------------------------------------------

def test_a_line_comment_on_a_where_condition():
    assert _fixed_point("select a from t where b = 1 -- why\n  and c = 2;") == (
        "SELECT a\nFROM t\nWHERE b = 1 -- why\n  AND c = 2;")


def test_one_comment_per_condition():
    assert _fixed_point(
        "select a from t where b = 1 -- one\n  and c = 2 -- two\n  and d = 3;") == (
        "SELECT a\nFROM t\nWHERE b = 1 -- one\n  AND c = 2 -- two\n  AND d = 3;")


def test_a_block_comment_leading_a_condition():
    """Inline, like a block comment in the select list — it is the LINE form
    that cannot share a row with what follows it."""
    assert fmt("select a from t where b = 1 and /* blk */ c = 2;") == (
        "SELECT a\nFROM t\nWHERE b           = 1\n  AND /* blk */ c = 2;")


def test_having_and_qualify_too():
    """They are the same block, so they came along with WHERE."""
    assert fmt("select a from t group by a having count(*) > 1 -- h\n"
               " and sum(b) > 2;").endswith(
        "HAVING COUNT(*) > 1 -- h\n   AND SUM(b)   > 2;")
    assert fmt("select a from t qualify row_number() over (order by a) = 1 -- q\n;",
               "redshift").endswith("= 1; -- q")


def test_a_comment_in_both_the_select_list_and_the_where():
    assert _fixed_point("select a -- s\n from t where b = 1 -- w\n  and c = 2;") == (
        "SELECT a -- s\nFROM t\nWHERE b = 1 -- w\n  AND c = 2;")


@pytest.mark.parametrize("sql,expect", [
    ("select a from t where (b = 1 or c = 2) -- grp\n  and d = 3;",
     "WHERE (b    = 1\n       OR c = 2) -- grp\n  AND d = 3;"),
    ("select a from t where b in (select x from u) -- sub\n  and c = 2;",
     "WHERE b IN (SELECT x\n            FROM u) -- sub\n  AND c = 2;"),
])
def test_conditions_that_span_rows(sql, expect):
    """A group and an `IN (SELECT …)` each leave their own LAST row on top, and
    the trailing comment belongs on that row — beside the closing paren, not on
    the block's first row where the condition started."""
    assert _fixed_point(sql).endswith(expect)


def test_a_boolean_inside_parens_is_not_a_slot_boundary():
    """The splitter counts depth, so `(c = 2 or d = 3)` stays one condition and
    a comment after it attaches to that condition rather than to a phantom."""
    from sqlalign.layout.comments import _where_slots

    src = "select a from t where b = 1 and (c = 2 or d = 3) and e = 4"
    assert len(_where_slots(src, "where")) == 3


# ---- the terminator ------------------------------------------------------

def test_the_terminator_goes_before_a_trailing_line_comment():
    """It used to be appended after, which put the `;` INSIDE the comment."""
    assert fmt("select a from t where b = 1 and c = 2 -- last\n;").endswith(
        "AND c = 2; -- last")


def test_the_unterminated_statement_really_would_swallow_the_next_one():
    """The premise, checked rather than trusted: with the `;` inside the comment
    the file has two statements and the splitter sees one."""
    from sqlalign.formatter import split_statements

    def count(text):
        return len([s for s in split_statements(text, "postgres") if s.strip()])

    assert count("SELECT a\nFROM t -- x;\nSELECT c FROM u;") == 1   # the old output
    assert count("SELECT a\nFROM t; -- x\nSELECT c FROM u;") == 2   # the new one


def test_a_trailing_comment_does_not_grow_the_gap_between_statements():
    """The joiner counted only the FOLLOWING statement's leading newlines. A
    statement ending in a comment ends with its own, so the gap grew by a blank
    line on every pass."""
    assert _fixed_point("select a from t where b = 1 -- x\n;\nselect c from u;") == (
        "SELECT a\nFROM t\nWHERE b = 1; -- x\n\nSELECT c\nFROM u;")


def test_ordinary_statement_spacing_is_unchanged():
    assert fmt("select a from t;\nselect c from u;") == (
        "SELECT a\nFROM t;\n\nSELECT c\nFROM u;")
    assert fmt("select a from t; select c from u;") == (
        "SELECT a\nFROM t; SELECT c\nFROM u;")


def test_an_empty_semicolon_is_not_a_statement():
    """Moving the comment past the `;` makes sqlglot yield a trailing
    `exp.Semicolon` carrying it. `_normalize` strips comments, so that node
    normalizes to nothing — counting it would have made the two spellings
    compare unequal purely because the comment changed sides."""
    assert ast_equal("select a -- note\n;", "SELECT a; -- note", "postgres")
    assert not ast_equal("select a; select b;", "select a;", "postgres")


# ---- what still declines, and why ----------------------------------------

@pytest.mark.parametrize("sql", [
    "select a from t group by 1, 2 -- positional\n;",
    "select coalesce(a /* mid */, 0) from t;",
])
def test_unmodelled_positions_still_decline(sql):
    """Explicitly, and byte-identical.

    A POSITIONAL `GROUP BY 1, 2` keeps every term on one line, so a comment
    against a term has no row of its own. A comment buried INSIDE an expression
    has no row either — it is between two things the layout renders as a single
    segment. This engine reproduces a comment faithfully or declines; it never
    guesses a position.
    """
    result = format_sql(sql, "postgres")
    assert result.warnings
    assert result.text == sql


@pytest.mark.parametrize("sql,expect", [
    ("select a from t -- f\n where b = 1;", "FROM t -- f\nWHERE b = 1;"),
    ("select a from t join u on u.i = t.i -- j\n where b = 1;",
     "JOIN u ON u.i = t.i -- j\nWHERE b = 1;"),
    ("select a from t -- f\n join u on u.i = t.i;", "FROM t -- f\nJOIN u ON u.i = t.i;"),
    ("select a from t, u -- c\n where b = 1;", "   , u -- c\nWHERE b = 1;"),
])
def test_from_and_join_rows(sql, expect):
    """FROM is one row per table reference. The split is on commas and on the
    START of a join clause — at `LEFT`, not at `JOIN` — so a comment written
    before `LEFT JOIN` stays with the row above it rather than jumping a line."""
    assert _fixed_point(sql).endswith(expect)


def test_a_comment_on_a_derived_table_row():
    """Every FROM branch routes through `attach_comments`, because a branch that
    forgets to does not decline — it DROPS the comment, which `ast_equal` cannot
    see. The derived-table branch did exactly that."""
    out = _fixed_point("select a from (select x from u) d -- der\n where b = 1;")
    assert "     ) d -- der" in out, out


def test_a_comment_in_every_clause_at_once():
    assert _fixed_point(
        "select a -- s\n from t -- f\n where b = 1 -- w\n"
        " group by a -- g\n , c order by a -- o\n , c;") == (
        "SELECT a -- s\n"
        "FROM t -- f\n"
        "WHERE b = 1 -- w\n"
        "GROUP BY a -- g\n"
        "       , c\n"
        "ORDER BY a -- o\n"
        "       , c;"
    )


@pytest.mark.parametrize("sql,expect", [
    ("select a, b from t group by a -- g\n , b;", "GROUP BY a -- g\n       , b;"),
    ("select a from t order by a -- o\n , b;", "ORDER BY a -- o\n       , b;"),
    ("select a from t order by a desc -- o\n;", "ORDER BY a DESC; -- o"),
    ("select a from t group by a -- g\n;", "GROUP BY a; -- g"),
])
def test_group_by_and_order_by(sql, expect):
    """They are comma lists like the select list, but laid out from rendered
    TEXT rather than from nodes — so grouporder.py lifts the annotations off and
    hands them to `comma_clause`, which is what the nodes were missing."""
    assert _fixed_point(sql).endswith(expect)


def test_a_single_term_clause_is_not_the_shared_row_case():
    """One term is one row, so a comment places against it exactly as it does
    for ORDER BY. Only the positional multi-term shape shares a line."""
    assert fmt("select a from t group by a -- g\n;").endswith("GROUP BY a; -- g")
    assert fmt("select a from t group by 1 -- g\n;").endswith("GROUP BY 1; -- g")


# ---- invariants ----------------------------------------------------------

SHAPES = [
    "select a from t where b = 1 -- why\n  and c = 2;",
    "select a from t where b = 1 and c = 2 -- last\n;",
    "select a from t where b = 1 and /* blk */ c = 2;",
    "select a -- s\n from t where b = 1 -- w\n  and c = 2;",
    "select a from t where (b = 1 or c = 2) -- grp\n  and d = 3;",
]


@pytest.mark.parametrize("sql", SHAPES)
def test_semantics_and_idempotence(sql):
    out = _fixed_point(sql)
    assert ast_equal(sql, out, "postgres")


@pytest.mark.parametrize("sql", SHAPES)
def test_the_comment_survives(sql):
    """`ast_equal` cannot see a dropped comment — only this can."""
    out = fmt(sql)
    for marker in ("-- why", "-- last", "/* blk */", "-- s", "-- w", "-- grp"):
        if marker in sql:
            assert marker in out, f"{marker} lost from {out!r}"


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected
