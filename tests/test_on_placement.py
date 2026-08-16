"""`Style.on_placement` — whether a JOIN's ON rides the table-reference row.

    inline (house)                        own_line
    JOIN orders ord ON ord.id = c.id      JOIN orders ord
                                            ON ord.id = c.id

8 of 10 surveyed guides state a placement and they split. `own_line` retires the
FROM-block-global ON column by definition — there is no longer an ON sitting
after each alias to align — so ON/AND are justified with plain arithmetic instead,
against a width taken across the WHOLE block so every join's ON shares one column.
Aliases and the block-global operator column still align either way.

Also covers the derived-table JOIN path (subquery.py), which has its own ON
emission and which NO golden exercises with a multi-condition ON — these tests are
that path's only coverage.
"""
import re

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import HOUSE, Style

OWN_LINE = Style(on_placement="own_line")

# A derived-table join with a multi-condition ON. No fixture has this shape, so
# without this literal the whole subquery.py ON path is untested.
DERIVED = ("select tt.a from things tt join (select x, y from u) d "
           "on d.x = tt.a and d.y = tt.b where tt.z = 1;")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---- invariants across every golden ----------------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_own_line_preserves_content_and_semantics(sid):
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    out = format_sql(inp, dialect, OWN_LINE).text
    assert _flat(out) == _flat(format_sql(inp, dialect, HOUSE).text)
    assert ast_equal(inp, out, dialect)
    assert format_sql(out, dialect, OWN_LINE).text == out


# ---- geometry --------------------------------------------------------------

def test_on_drops_below_the_table_reference():
    out = format_sql(load_pair("13")[0], "postgres", OWN_LINE).text
    assert "INNER JOIN orders            ord\n   ON ord.customer_id" in out


def test_every_on_shares_one_column_across_the_block():
    """Justifying per join would put ON at a different column depending on
    whether that join happens to have a continuation."""
    out = format_sql(load_pair("13")[0], "postgres", OWN_LINE).text
    on_cols = {ln.index("ON ") for ln in out.split("\n") if ln.lstrip().startswith("ON ")}
    assert len(on_cols) == 1, on_cols


def test_and_ends_where_on_ends():
    out = format_sql(load_pair("13")[0], "postgres", OWN_LINE).text
    on = next(ln for ln in out.split("\n") if ln.lstrip().startswith("ON addr"))
    and_ = next(ln for ln in out.split("\n") if ln.lstrip().startswith("AND addr"))
    assert on.index("ON") + len("ON") == and_.index("AND") + len("AND")


def test_aliases_and_operator_column_still_align():
    out = format_sql(load_pair("13")[0], "postgres", OWN_LINE).text
    assert "FROM customers               cust" in out          # alias column intact
    # Only the FROM block: the WHERE clause's operators are a SEPARATE alignment
    # scope and correctly have their own column, so sweeping the whole statement
    # would compare two columns that were never meant to match.
    lines = out.split("\n")
    block = lines[lines.index("FROM customers               cust"):
                  next(i for i, ln in enumerate(lines) if ln.startswith("WHERE"))]
    eq_cols = {ln.index(" = ") for ln in block
               if ln.lstrip().startswith(("ON ", "AND ")) and " = " in ln}
    assert len(eq_cols) == 1, eq_cols                          # operator column intact


def test_inline_is_unchanged():
    inp, expected = load_pair("13")
    assert format_sql(inp, "postgres", HOUSE).text == expected


# ---- derived-table joins (subquery.py) -------------------------------------

def test_derived_join_honours_own_line():
    out = format_sql(DERIVED, "postgres", OWN_LINE).text
    assert ") d\n" in out                     # alias row ends there
    assert re.search(r"\n\s+ON d\.x = tt\.a", out)


def test_derived_join_honours_trailing_booleans():
    """Regression: this path was missed when boolean_operator_position landed,
    so a file with both a derived join and a WHERE got MIXED conventions. No
    golden has this shape, so no cross-golden invariant could have caught it."""
    out = format_sql(DERIVED, "postgres",
                     Style(boolean_operator_position="trailing")).text
    assert "d.x = tt.a AND\n" in out
    for line in out.split("\n"):
        assert not re.match(r"^\s*(AND|OR)\b", line), line


def test_derived_join_default_is_unchanged():
    out = format_sql(DERIVED, "postgres", HOUSE).text
    assert ") d ON d.x = tt.a\n" in out
    assert re.search(r"\n\s+AND d\.y = tt\.b", out)


def test_derived_join_preserves_semantics_under_every_combination():
    for style in (HOUSE, OWN_LINE,
                  Style(boolean_operator_position="trailing"),
                  Style(on_placement="own_line", boolean_operator_position="trailing")):
        out = format_sql(DERIVED, "postgres", style).text
        assert ast_equal(DERIVED, out, "postgres")
        assert format_sql(out, "postgres", style).text == out


# ---- composition + validation ----------------------------------------------

def test_composes_with_trailing_booleans():
    out = format_sql(load_pair("13")[0], "postgres",
                     Style(on_placement="own_line",
                           boolean_operator_position="trailing")).text
    assert "= ord.order_id AND\n" in out
    for line in out.split("\n"):
        assert not re.match(r"^\s*(AND|OR)\b", line), line


def test_house_default_is_inline():
    assert HOUSE.on_placement == "inline"


@pytest.mark.parametrize("bad", ["own line", "ownline", "below", ""])
def test_invalid_value_rejected(bad):
    with pytest.raises(ValueError):
        Style(on_placement=bad)
