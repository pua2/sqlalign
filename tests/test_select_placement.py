"""`Style.select_placement` — the first select item rides the SELECT line
(house) or the list starts below it.

    inline (house)      own_line
    SELECT a            SELECT
         , b                a,
                            b

This is the dominant convention wherever SQL is generated rather than typed —
dbt's style guide, GitLab's, and most analytics-engineering repos — because
adding a column then touches one line in the diff instead of two.

The interesting part is not the SELECT line, it is that four modules had their
own copy of `_ITEM_COL = len("SELECT ")`: case.py, window.py, expr.py and
select.py each computed the item column independently, which was true only while
the list always began on the SELECT line. They now share
`layout.select_item_col`, so a CASE, a window and a scalar subquery keep agreeing
with the plain items around them at any indent. Those three shapes are the ones
this file guards hardest.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import HOUSE, Style

OWN = Style(select_placement="own_line")                       # the default 2
OWN4 = Style(select_placement="own_line", select_indent=4)     # dbt's width
OWN_TRAIL = Style(select_placement="own_line", comma_position="trailing")


def fmt(sql, style, dialect="postgres"):
    result = format_sql(sql, dialect, style)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- the shape -----------------------------------------------------------

def test_leading_commas_hang_two_columns_back():
    """The same two columns they hang back by inline."""
    assert fmt("select a, bb, ccc from t;", OWN) == (
        "SELECT\n"
        "  a\n"
        ", bb\n"
        ", ccc\n"
        "FROM t;"
    )


def test_trailing_commas_stack_flush():
    assert fmt("select a, bb, ccc from t;", OWN_TRAIL) == (
        "SELECT\n"
        "  a,\n"
        "  bb,\n"
        "  ccc\n"
        "FROM t;"
    )


def test_indent_is_configurable():
    assert fmt("select a, bb from t;", OWN4).startswith("SELECT\n    a\n  , bb")


def test_inline_is_unchanged():
    sql = "select a, bb from t;"
    assert fmt(sql, HOUSE) == "SELECT a\n     , bb\nFROM t;"


def test_house_default_is_inline():
    assert HOUSE.select_placement == "inline"
    assert HOUSE.select_indent == 2


# ---- the multi-line item shapes, which is where the item column mattered --

def test_a_case_item_follows_the_list_indent():
    sql = ("select id, case when a = 1 then 'one' when a = 2 then 'two' else 'other' end as label "
           "from t;")
    out = fmt(sql, OWN_TRAIL)
    body = [ln for ln in out.split("\n") if "WHEN" in ln or "ELSE" in ln]
    assert body, out
    # every CASE body line sits at or past the list indent, never back at column 0
    assert all(len(ln) - len(ln.lstrip()) >= 2 for ln in body), out


def test_a_scalar_subquery_item_opens_at_the_item_column():
    sql = "select id, (select max(x) from z where z.k = t.k) as mx from t;"
    out = fmt(sql, OWN_TRAIL)
    open_line = next(ln for ln in out.split("\n") if "(SELECT" in ln)
    assert open_line.index("(SELECT") == 2, out


def test_a_window_item_follows_the_list_indent():
    sql = ("select id, row_number() over (partition by a, b order by c desc) as rn from t;")
    out = fmt(sql, OWN_TRAIL)
    assert "SELECT\n  id,\n  ROW_NUMBER()" in out, out


@pytest.mark.parametrize("indent", [2, 4, 8])
def test_multiline_items_track_any_indent(indent):
    sql = "select id, (select max(x) from z) as mx from t;"
    out = fmt(sql, Style(select_placement="own_line", select_indent=indent,
                         comma_position="trailing"))
    open_line = next(ln for ln in out.split("\n") if "(SELECT" in ln)
    assert open_line.index("(SELECT") == indent, out


# ---- invariants ----------------------------------------------------------

@pytest.mark.parametrize("style", [OWN, OWN4, OWN_TRAIL])
@pytest.mark.parametrize("sid", SAMPLES)
def test_content_semantics_and_idempotence(sid, style):
    inp, expected = load_pair(sid)
    dialect = DIALECTS.get(sid, "postgres")
    result = format_sql(inp, dialect, style)
    if result.warnings:
        pytest.skip("declined under this style, which is a safe outcome")
    out = result.text
    # Compare with the comma detached: `comma_position` legitimately glues it to
    # the preceding token, so a naive whitespace split would report that as a
    # token change when it is exactly the whitespace change under test.
    def tokens(text):
        return text.replace(",", " , ").split()

    assert tokens(out) == tokens(expected), "tokens moved, not just whitespace"
    assert ast_equal(inp, out, dialect)
    assert format_sql(out, dialect, style).text == out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged_by_the_default(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


# ---- validation ----------------------------------------------------------

@pytest.mark.parametrize("bad", ["own line", "ownline", "OWN_LINE", "", True])
def test_rejects_bad_placement(bad):
    with pytest.raises(ValueError):
        Style(select_placement=bad)


@pytest.mark.parametrize("bad", [0, -1, "4", 2.5, True, None])
def test_rejects_bad_indent(bad):
    with pytest.raises(ValueError):
        Style(select_indent=bad)


def test_config_and_cli(tmp_path):
    from sqlalign.cli import main

    sql = tmp_path / "q.sql"
    sql.write_text("select a, b from t;\n")
    assert main(["--select-placement", "own_line", "--select-indent", "2", str(sql)]) == 0
    assert sql.read_text().startswith("SELECT\n  a\n, b")

    (tmp_path / ".sqlalign.toml").write_text(
        'select_placement = "own_line"\nselect_indent = 6\n')
    sql.write_text("select a, b from t;\n")
    assert main([str(sql)]) == 0
    assert sql.read_text().startswith("SELECT\n      a\n    , b")
