"""`Style.boolean_operator_position` — leading (house) vs trailing AND/OR.

    WHERE a = 1            WHERE a = 1 AND
      AND b = 2                  b = 2

7 of 10 surveyed guides state a position. This was a prerequisite for making the
FROM-block ON/AND column configurable at all: that column exists to align the
AND at each continuation row's head, so "what if there is no AND there" had to be
answered first. The answer is a zero-width segment still tagged `kind="on"` —
the resolver right-aligns its (empty) end to the same ON column, which lands the
continuation's condition in exactly the first condition's column. The column
survives under either setting.
"""
import re

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import HOUSE, Style

TRAILING = Style(boolean_operator_position="trailing")


def _flat(text: str) -> str:
    """All whitespace collapsed, newlines included. Moving a boolean from the
    head of one row to the tail of the row above leaves this unchanged, so any
    difference is a real content or ordering change."""
    return re.sub(r"\s+", " ", text).strip()


# ---- invariants across every golden ----------------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_trailing_preserves_content_and_order(sid):
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    assert (_flat(format_sql(inp, dialect, TRAILING).text)
            == _flat(format_sql(inp, dialect, HOUSE).text))


@pytest.mark.parametrize("sid", SAMPLES)
def test_trailing_preserves_semantics(sid):
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    assert ast_equal(inp, format_sql(inp, dialect, TRAILING).text, dialect)


@pytest.mark.parametrize("sid", SAMPLES)
def test_trailing_is_idempotent(sid):
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    once = format_sql(inp, dialect, TRAILING).text
    assert format_sql(once, dialect, TRAILING).text == once


@pytest.mark.parametrize("sid", SAMPLES)
def test_no_line_opens_with_a_boolean(sid):
    """The point of the option."""
    inp = load_pair(sid)[0]
    out = format_sql(inp, DIALECTS.get(sid, "postgres"), TRAILING).text
    for line in out.split("\n"):
        assert not re.match(r"^\s*(AND|OR)\b", line), line


# ---- the prerequisite: the ON/AND column survives ---------------------------

def test_join_continuation_still_aligns_with_the_first_condition():
    out = format_sql(load_pair("13")[0], "postgres", TRAILING).text
    on_line = next(ln for ln in out.split("\n") if " ON addr.order_id" in ln)
    cont = next(ln for ln in out.split("\n") if "addr.address_type" in ln)
    first_cond_col = on_line.index("addr.order_id")
    assert cont.index("addr.address_type") == first_cond_col


def test_join_operator_column_survives_trailing_booleans():
    """The block-global `=` column must still line up once the AND has moved."""
    out = format_sql(load_pair("13")[0], "postgres", TRAILING).text
    eq_cols = {ln.index(" = ") for ln in out.split("\n") if (" = " in ln and "ON " in ln) or
               ln.strip().startswith("addr.address_type")}
    assert len(eq_cols) == 1, eq_cols


def test_join_geometry():
    out = format_sql(load_pair("13")[0], "postgres", TRAILING).text
    assert "= ord.order_id AND\n" in out
    assert "\n                                           addr.address_type   = 'shipping'" in out


# ---- geometry per clause ---------------------------------------------------

def test_where_geometry():
    out = format_sql("select a from t where xx = 1 and y = 2;", "postgres", TRAILING).text
    assert out == "SELECT a\nFROM t\nWHERE xx = 1 AND\n      y  = 2;"


def test_where_leading_is_unchanged():
    out = format_sql("select a from t where xx = 1 and y = 2;", "postgres", HOUSE).text
    assert out == "SELECT a\nFROM t\nWHERE xx = 1\n  AND y  = 2;"


def test_having_geometry():
    out = format_sql(load_pair("05")[0], "postgres", TRAILING).text
    assert "HAVING COUNT(*)   > 3 AND\n       SUM(total) > 1000" in out


def test_compound_case_when_geometry():
    out = format_sql(load_pair("08")[0], "postgres", TRAILING).text
    assert "WHEN status = 'pending' AND\n                 NOT is_archived" in out


def test_nested_group_booleans_also_move():
    """A parenthesized group's internal boolean must move too, or one file ends
    up with both conventions. The group's first condition sits inline on the
    caller's row, so that first boolean lands there rather than on a row of the
    group's own."""
    out = format_sql(load_pair("04")[0], "postgres", TRAILING).text
    assert "'accessories') OR\n" in out           # group's own boolean moved
    assert "name LIKE '%refurb%') AND\n" in out   # enclosing boolean moved too


def test_condition_columns_are_identical_in_both_modes():
    """Deliberate: only the operator moves. A continuation keeps the exact column
    it had under leading booleans (inside a group that means it stays offset past
    the group's first condition, where the OR used to sit) -- the alternative,
    re-flowing columns, would make the two modes diverge in more than one way."""
    inp = load_pair("04")[0]
    def cols(text, needle):
        return [ln.index(needle) for ln in text.split("\n") if needle in ln]
    lead = format_sql(inp, "postgres", HOUSE).text
    trail = format_sql(inp, "postgres", TRAILING).text
    assert cols(lead, "name LIKE") == cols(trail, "name LIKE")
    assert cols(lead, "price ") == cols(trail, "price ")


def test_or_is_handled_too():
    out = format_sql("select a from t where xx = 1 or y = 2;", "postgres", TRAILING).text
    assert "WHERE xx = 1 OR\n      y  = 2;" in out


# ---- composition + validation ----------------------------------------------

def test_composes_with_comma_and_align_options():
    style = Style(boolean_operator_position="trailing", comma_position="trailing",
                  align=False)
    out = format_sql("select a, b from t where xx = 1 and y = 2;", "postgres", style).text
    assert out == "SELECT a,\n       b\nFROM t\nWHERE xx = 1 AND\n      y = 2;"


def test_house_default_is_leading():
    assert HOUSE.boolean_operator_position == "leading"


@pytest.mark.parametrize("bad", ["Leading", "end", "after", ""])
def test_invalid_value_rejected(bad):
    with pytest.raises(ValueError):
        Style(boolean_operator_position=bad)
