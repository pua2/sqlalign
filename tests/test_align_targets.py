"""`Style.align_targets` — which alignment columns participate.

The style-knob catalog proposed sixteen separate `align_*` booleans. They are one
option with one mechanism (a segment aligns only if it carries both a scope and a
kind, so switching a target off just clears them), so they are exposed as a single
list keyed by SQL vocabulary — `aliases`, `table_names`, `operators`,
`join_conditions`, `case_results`, `column_types`, `column_constraints` — rather
than by internal kind names. Collapsing them before release matters: once a name is in a committed
config file, renaming it breaks every repo that has one.
"""
import re

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import ALL_ALIGN_TARGETS, HOUSE, HOUSE_ALIGN_TARGETS, Style


def _content(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text)


def _style(*targets):
    return Style(align_targets=frozenset(targets))


# ---- the default is unchanged ----------------------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_all_targets_reproduces_the_goldens(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres"), HOUSE).text == expected


def test_house_enables_every_target_except_the_opt_in_ones():
    """`table_names` pads the FROM/JOIN keyword so the table names share a
    column. That is a different published style, not a refinement of this one,
    so it is a legal target that the house style does not ship with — and the
    goldens above prove the distinction is load-bearing."""
    assert HOUSE.align_targets == HOUSE_ALIGN_TARGETS
    assert HOUSE_ALIGN_TARGETS < ALL_ALIGN_TARGETS
    assert {"table_names"} == ALL_ALIGN_TARGETS - HOUSE_ALIGN_TARGETS


# ---- turning targets off ---------------------------------------------------

# Operand widths deliberately differ (`a.zzz` vs `a.w`), so operator padding is
# observable — with equal widths the column is satisfied without any padding and
# the assertion would pass whether or not the target did anything.
SQL = ("select a.x as one, b.yy as two from t a join u b on b.id = a.id "
       "and b.k = a.k where a.zzz = 1 and a.w = 2;")


def test_only_aliases_keeps_the_as_column_and_drops_the_rest():
    out = format_sql(SQL, "postgres", _style("aliases")).text
    assert "a.x  AS one" in out          # alias column still padded
    assert "  AND b.k = a.k" in out      # join-condition column collapsed
    assert "AND a.w = 2" in out          # operator column collapsed


def test_only_operators_keeps_the_operator_column():
    out = format_sql(SQL, "postgres", _style("operators")).text
    assert "a.x AS one" in out           # alias column collapsed
    assert "AND a.w   = 2" in out        # operator column still padded


def test_disabling_join_conditions_collapses_the_on_column():
    full = format_sql(SQL, "postgres", HOUSE).text
    without = format_sql(SQL, "postgres",
                         _style(*(ALL_ALIGN_TARGETS - {"join_conditions"}))).text
    assert "        AND b.k" in full     # ON/AND column padded by default
    assert "        AND b.k" not in without


def test_column_types_and_constraints_are_separable():
    inp = load_pair("14")[0]             # CREATE TABLE with types + constraints
    types_only = format_sql(inp, "postgres", _style("column_types")).text
    assert "report_date DATE" in types_only
    assert "DATE           NOT NULL" not in types_only   # constraint column off


# ---- empty targets is exactly `align=False` --------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_no_targets_equals_align_off(sid):
    """Two spellings of the same thing must agree, or one of them is lying."""
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    assert (format_sql(inp, dialect, Style(align_targets=frozenset())).text
            == format_sql(inp, dialect, Style(align=False)).text)


@pytest.mark.parametrize("bools", ["leading", "trailing"])
def test_no_targets_equals_align_off_under_either_boolean_position(bools):
    """The parametrized case above only ever sees LEADING booleans -- every
    golden uses them -- and the two spellings diverged under trailing ones.

    With booleans trailing, fromjoin.py heads a continuation row with an empty
    segment tagged `on`, purely to hold the ON column. `align=False` skips it.
    `align_targets=frozenset()` instead UNTAGS it (apply_align_targets clears
    scope/kind rather than removing the segment), leaving a zero-width untagged
    segment that still drew a separator space -- so the continuation indented
    three instead of two.
    """
    sql = "select 1 from a x join b y on y.id = x.id and y.k = 'z';"
    no_targets = format_sql(sql, "postgres", Style(
        align_targets=frozenset(), boolean_operator_position=bools)).text
    align_off = format_sql(sql, "postgres", Style(
        align=False, boolean_operator_position=bools)).text
    assert no_targets == align_off


def test_a_disabled_column_leaves_no_gap_behind_it():
    """Turning a target off must collapse its column to a single space, not to
    a space plus the width of whatever used to hold it."""
    sql = "select 1 from a x join b y on y.id = x.id and y.k = 'z';"
    out = format_sql(sql, "postgres", Style(
        align_targets=frozenset({"aliases", "operators"}),
        boolean_operator_position="trailing")).text
    cont = next(ln for ln in out.split("\n") if "y.k" in ln)
    assert cont.startswith("  y.k"), f"phantom gap: {cont!r}"


# ---- invariants hold for any subset ----------------------------------------

@pytest.mark.parametrize("targets", [
    frozenset(), {"aliases"}, {"operators"}, {"aliases", "operators"},
    ALL_ALIGN_TARGETS - {"join_conditions"}, ALL_ALIGN_TARGETS,
])
@pytest.mark.parametrize("sid", ["13", "08", "14", "06"])
def test_subsets_preserve_content_semantics_and_idempotency(sid, targets):
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    style = Style(align_targets=frozenset(targets))
    out = format_sql(inp, dialect, style).text
    # only horizontal padding differs from the fully-aligned form
    assert _content(out) == _content(format_sql(inp, dialect, HOUSE).text)
    assert ast_equal(inp, out, dialect)
    assert format_sql(out, dialect, style).text == out


# ---- validation ------------------------------------------------------------

@pytest.mark.parametrize("bad", ["alias", "ops", "AS", "join-conditions", "everything"])
def test_unknown_target_rejected(bad):
    with pytest.raises(ValueError) as e:
        Style(align_targets=frozenset({bad}))
    assert bad in str(e.value)
    assert "valid:" in str(e.value)      # the error names the valid set


def test_error_lists_valid_targets():
    with pytest.raises(ValueError) as e:
        Style(align_targets=frozenset({"nope"}))
    for name in ALL_ALIGN_TARGETS:
        assert name in str(e.value)
