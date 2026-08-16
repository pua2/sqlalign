"""Subquery layout: gated regressions.

FIX 1 — the silent IN-subquery anchor bug (HIGH): `_render_in_subquery`
(conditions.py) used to tag the `IN` keyword into the WHERE clause's shared
`op` scope, like every ordinary comparison operator. When a sibling
condition in the same scope had a longer LHS, the resolver padded `IN`
rightward at render time -- but the subquery body's own line indents are
baked in from `IN`'s NATURAL (unpadded) column (subquery.py's geometry is
precomputed, not resolver-driven; see its module docstring), so the body
silently detached from its own `(`. `ast_equal` stayed True (the bug is pure
whitespace) and no warning fired, so this was invisible to the safety net.

Fix: `IN` is now emitted UNTAGGED (`Seg("IN")`, no scope/kind) in the
subquery shape only -- excluded from operator right-alignment, matching
`EXISTS` (already untagged) and the derived-table operator-scope
exclusion. Plain `IN (list)` (`predicate_segs`) is a different code path and
is unaffected -- it still right-aligns with sibling operators
(`test_in_list_still_right_aligns` guards this).

The same root cause (one shared `_render_in_subquery`) also mis-anchored a
paren-group-nested `IN (SELECT ...)` -- spot-checked manually (not gated
here, since `_render_group` recurses through the identical function; the two
gated WHERE-level cases below exercise the fixed code path directly).

FIX 2 — EXISTS gated tests: EXISTS was already implemented correctly (its
keyword was always untagged) but shipped with zero fixtures/tests. NOT
EXISTS is not implemented; it safely declines to a byte-identical
passthrough (predicate_segs has no exp.Not case, so `_render_condition`
raises Unsupported via the `op is None` fallback) -- the negation is
therefore never at risk of being dropped, since the statement is never
rewritten at all.
"""
import pytest

from sqlalign.formatter import ast_equal, format_sql


def test_in_subquery_body_anchors_under_longer_sibling():
    # WHERE with IN-subquery AFTER a longer-LHS sibling: previously the padded
    # `IN` (right-aligned to match `=`'s far-right end column) left the
    # subquery body's baked-in natural indent (col 12) stranded to the left
    # of the actual, now-shifted "(" -- this asserts the corrected, anchored
    # output instead.
    sql = ("select a from t where some_very_long_column_name = 1 "
           "and x in (select id from u where p = 1 and q = 2);")
    out = format_sql(sql).text
    expected = (
        "SELECT a\n"
        "FROM t\n"
        "WHERE some_very_long_column_name = 1\n"
        "  AND x IN (SELECT id\n"
        "            FROM u\n"
        "            WHERE p = 1\n"
        "              AND q = 2);"
    )
    assert out == expected
    # `FROM u` / `WHERE p = 1` (col 12) align exactly under `SELECT id`'s `S`
    # (also col 12, since "  AND x IN (" is 12 chars) -- the body anchors
    # under its own subquery, not the pre-fix stale natural column.
    lines = out.splitlines()
    select_col = lines[3].index("SELECT")
    assert lines[4][:select_col].strip() == "" and lines[4][select_col:].startswith("FROM u")
    assert lines[5][:select_col].strip() == "" and lines[5][select_col:].startswith("WHERE p = 1")

    assert format_sql(out).text == out                    # idempotent
    assert ast_equal(sql, out, "postgres")                 # meaning preserved


def test_in_subquery_first_with_longer_later_sibling():
    # IN-subquery comes FIRST in the WHERE, the longer LHS sibling comes
    # after (op scope is resolved over the whole clause regardless of
    # source order, so this is the mirror-image trigger of the case above).
    sql = "select a from t where t.id in (select id from u) and some_very_long_col = 1;"
    out = format_sql(sql).text
    expected = (
        "SELECT a\n"
        "FROM t\n"
        "WHERE t.id IN (SELECT id\n"
        "               FROM u)\n"
        "  AND some_very_long_col = 1;"
    )
    assert out == expected
    lines = out.splitlines()
    select_col = lines[2].index("SELECT")
    assert lines[3][:select_col].strip() == "" and lines[3][select_col:].startswith("FROM u)")

    assert format_sql(out).text == out
    assert ast_equal(sql, out, "postgres")


def test_in_list_still_right_aligns():
    # Regression guard for the *other* IN path (predicate_segs): plain
    # IN (list) must keep right-aligning with a sibling operator in the same
    # WHERE scope -- this fix only touches the IN-subquery shape.
    sql = "select a from t where some_long_column = 1 and x in ('a', 'b');"
    out = format_sql(sql).text
    expected = (
        "SELECT a\n"
        "FROM t\n"
        "WHERE some_long_column = 1\n"
        "  AND x               IN ('a', 'b');"
    )
    assert out == expected
    # `=` and `IN` end at the same column (right-aligned within the WHERE's
    # shared `op` scope), exactly as before this fix.
    lines = out.splitlines()
    assert lines[2].index("=") + 1 == lines[3].index("IN") + 2

    assert format_sql(out).text == out
    assert ast_equal(sql, out, "postgres")


def test_exists_subquery_formats():
    sql = "select a from t where exists (select 1 from u where u.tid = t.id);"
    r = format_sql(sql)
    expected = (
        "SELECT a\n"
        "FROM t\n"
        "WHERE EXISTS (SELECT 1\n"
        "              FROM u\n"
        "              WHERE u.tid = t.id);"
    )
    assert r.text == expected
    assert r.warnings == []
    assert format_sql(r.text).text == r.text               # idempotent
    assert ast_equal(sql, r.text, "postgres")               # meaning preserved


def test_not_exists_formats():
    """It used to decline -- `predicate_segs` had no `exp.Not` case, so rather
    than risk dropping the negation the whole statement passed through. The
    negation is now read, and it lands where a reader looks for it."""
    sql = "select a from t where not exists (select 1 from u where u.tid = t.id);"
    r = format_sql(sql)
    assert r.warnings == []
    assert r.text == (
        "SELECT a\n"
        "FROM t\n"
        "WHERE NOT EXISTS (SELECT 1\n"
        "                  FROM u\n"
        "                  WHERE u.tid = t.id);"
    )
    assert ast_equal(sql, r.text, "postgres")


@pytest.mark.parametrize("sql,expect", [
    ("select a from t where x not in (select y from u);", "WHERE x NOT IN (SELECT y"),
    ("select a from t where x in (select y from u);", "WHERE x IN (SELECT y"),
])
def test_the_negation_rides_the_keyword_not_the_lhs(sql, expect):
    """`x NOT IN (...)`, never `NOT x IN (...)` -- both are valid and mean the
    same thing, but only one is how anyone writes it. It also has to be the
    keyword: the body's indents are baked in from the keyword's own width."""
    out = format_sql(sql).text
    assert expect in out, out
    assert ast_equal(sql, out, "postgres")
