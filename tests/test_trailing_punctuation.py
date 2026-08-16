"""Trailing punctuation must never land inside a trailing comment.

Two sites glue punctuation onto the END of a row: `joiner_head` under
`boolean_operator_position="trailing"`, and commas.py under
`comma_position="trailing"`. Both appended to the last segment blindly, and once
comments could reach those rows the last segment was sometimes a `--` comment:

    SELECT a -- c,          <- the comma is INSIDE the comment
           b                   ...so the select list has no separator

    WHERE b = 1 -- c AND    <- the AND is inside it
          d = 2;               ...so the second condition has no joiner

Both are the swallow bug that ate a select-list column, one layer over. Neither
is visible to `ast_equal` on its own -- it fired here only because the missing
separator changes the parse.

Found by a PRESET MATRIX, not by a sweep: every case passes under `house`,
because house puts commas and booleans at the START of a row where nothing
follows them. Only `trailing` (and `gitlab`, which shares the comma setting)
reaches these two lines at all.

The fix is structural rather than a text check for a leading `--`: comment
segments carry `kind=COMMENT_KIND`, and both sites split them off the end before
appending. `kind` alone is inert for alignment -- align.py only aligns a segment
carrying BOTH a scope and a kind, and a comment never gets a scope.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import PRESETS, Style, preset_style

TRAILING = Style(comma_position="trailing", boolean_operator_position="trailing")


def fmt(sql, style=TRAILING):
    result = format_sql(sql, "postgres", style)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- the two sites --------------------------------------------------------

def test_a_trailing_comma_goes_before_the_comment():
    assert fmt("select a -- c\n, b from t;") == "SELECT a, -- c\n       b\nFROM t;"


def test_a_trailing_boolean_goes_before_the_comment():
    assert fmt("select a from t where b = 1 -- c\n  and d = 2;") == (
        "SELECT a\nFROM t\nWHERE b = 1 AND -- c\n      d = 2;")


def test_a_comment_on_a_middle_row():
    assert fmt("select a, b -- c\n, c from t;") == (
        "SELECT a,\n       b, -- c\n       c\nFROM t;")


@pytest.mark.parametrize("sql,inside", [
    ("select a -- c\n, b from t;", "-- c,"),
    ("select a from t where b = 1 -- c\n  and d = 2;", "-- c AND"),
])
def test_the_broken_spelling_really_loses_its_separator(sql, inside):
    """The premise, checked rather than trusted: with the punctuation inside the
    comment the statement means something else, which is why the guard caught it
    rather than it shipping."""
    out = fmt(sql)
    assert inside not in out
    assert ast_equal(sql, out, "postgres")


def test_punctuation_is_unaffected_without_a_comment():
    assert fmt("select a, b from t where c = 1 and d = 2;") == (
        "SELECT a,\n       b\nFROM t\nWHERE c = 1 AND\n      d = 2;")


def test_the_tag_does_not_make_comments_align():
    """`kind` without a scope is inert — a comment must not acquire a column."""
    from sqlalign.ir import COMMENT_KIND, comment_seg

    seg = comment_seg("-- x")
    assert seg.kind == COMMENT_KIND and seg.scope is None


# ---- the matrix that found it ---------------------------------------------

CONSTRUCTS = [
    "select a -- s\n, b from t where c = 1 -- w\n  and d = 2;",
    "select a from t group by a -- g\n, b order by a -- o\n, b;",
    "select a from t, u -- j\n where b = 1;",
    "select a from (select x as a from u) d -- der\n where b = 1;",
    "select case x when 1 then 2 else 3 end from t;",
    "select a from t where b not like 'a%' and c is distinct from d;",
    "delete from t using u where t.i = u.i returning *;",
]


@pytest.mark.parametrize("preset", sorted(PRESETS))
@pytest.mark.parametrize("sql", CONSTRUCTS)
def test_every_preset_against_every_construct(preset, sql):
    """A single-preset sweep cannot find this class. House puts its commas and
    booleans at the START of a row, where nothing follows them."""
    style = preset_style(preset)
    result = format_sql(sql, "postgres", style)
    assert result.warnings == [], f"{preset} declined: {result.warnings}"
    assert ast_equal(sql, result.text, "postgres")
    assert format_sql(result.text, "postgres", style).text == result.text


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected
