"""`Style.comma_position` — leading (house) vs trailing separator commas.

10 of 10 surveyed style guides state a comma position and 8 say trailing, making
this the most-disagreed-on knob in the surface. Every separator comma is emitted
by its handler as its own `kind="comma"` Seg, and `commas.apply_comma_position`
relocates it on the IR before rendering — deliberately NOT a pass over printed
text, which is how the two shipped implementations of this option in the wild
(sql-formatter's `commaPosition` among them) ended up removed.

Verified by invariants across every golden plus explicit geometry pinning,
rather than by adding a second full set of byte-exact fixtures.
"""
import re

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import HOUSE, Style

TRAILING = Style(comma_position="trailing")


def _content(text: str) -> str:
    """Everything except whitespace and commas — the part comma position must
    not touch."""
    return re.sub(r"[\s,]+", "", text)


# ---- invariants across every golden ----------------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_trailing_preserves_content(sid):
    """INV: moving commas changes only where commas and spaces sit."""
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    leading = format_sql(inp, dialect, HOUSE).text
    trailing = format_sql(inp, dialect, TRAILING).text
    assert _content(leading) == _content(trailing)
    assert leading.count(",") == trailing.count(",")   # none gained or lost


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
def test_no_line_starts_with_a_comma_when_trailing(sid):
    """The point of the option: no continuation line may open with a comma."""
    inp = load_pair(sid)[0]
    out = format_sql(inp, DIALECTS.get(sid, "postgres"), TRAILING).text
    for line in out.split("\n"):
        assert not line.lstrip().startswith(","), line


# ---- geometry, pinned explicitly -------------------------------------------

def test_select_list_geometry():
    out = format_sql("select a, bb, ccc from t;", "postgres", TRAILING).text
    assert out == "SELECT a,\n       bb,\n       ccc\nFROM t;"


def test_leading_geometry_is_unchanged():
    out = format_sql("select a, bb, ccc from t;", "postgres", HOUSE).text
    assert out == "SELECT a\n     , bb\n     , ccc\nFROM t;"


def test_group_by_geometry():
    out = format_sql("select a, b, count(*) from t group by a, b;", "postgres", TRAILING).text
    assert "GROUP BY a,\n         b;" in out


def test_insert_column_list_keeps_its_paren():
    out = format_sql(load_pair("11")[0], "postgres", TRAILING).text
    assert "(  report_date,\n   channel," in out
    assert "order_count)" in out          # closing paren still tucked


def test_update_set_alignment_survives():
    out = format_sql(load_pair("11")[0], "postgres", TRAILING).text
    assert "SET price      = price * 1.05,\n    updated_at = CURRENT_TIMESTAMP" in out


def test_create_table_columns():
    out = format_sql(load_pair("14")[0], "postgres", TRAILING).text
    assert "report_date DATE           NOT NULL,\n" in out


def test_multiline_item_comma_lands_on_its_last_line():
    """The hard case: for a multi-line item (a CASE), the separator comma belongs
    on the item's LAST line, not the line where it started."""
    out = format_sql(load_pair("08")[0], "postgres", TRAILING).text
    assert "       END AS order_size,\n" in out
    assert "CASE WHEN total >= 1000 THEN 'large'," not in out   # not the first line


def test_cte_separator_comma_moves_to_the_closing_paren():
    """A CTE's comma head is a bare `,` against a `WITH ...` opener, so it must
    be dropped rather than blanked — otherwise every later CTE indents by two
    columns instead of starting at column 0."""
    out = format_sql(load_pair("06")[0], "postgres", TRAILING).text
    assert "),\n\ntop_customers AS (" in out
    assert "\n  top_customers" not in out          # not indented by the old comma


def test_commas_inside_expressions_are_untouched():
    """Only SEPARATOR commas move — a comma inside a type, a function call, or a
    string literal must stay exactly where it is."""
    sql = "select coalesce(a, b) as x, cast(y as numeric(12, 2)) as z, 'p,q' as s from t;"
    out = format_sql(sql, "postgres", TRAILING).text
    assert "COALESCE(a, b)" in out
    assert "NUMERIC(12, 2)" in out
    assert "'p,q'" in out


# ---- composition + validation ----------------------------------------------

def test_composes_with_no_align():
    out = format_sql("select a, bb, ccc from t;", "postgres",
                     Style(comma_position="trailing", align=False)).text
    assert out == "SELECT a,\n       bb,\n       ccc\nFROM t;"


def test_house_default_is_leading():
    assert HOUSE.comma_position == "leading"


@pytest.mark.parametrize("value", ["Leading", "trailing ", "end", ""])
def test_invalid_comma_position_rejected(value):
    with pytest.raises(ValueError):
        Style(comma_position=value)
