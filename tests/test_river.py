"""`Style.clause_keyword_align = "river"` — root clause keywords right-aligned
to a gutter, so every clause body shares one column.

    SELECT r.last_name
      FROM riders r
     WHERE r.id > 0

The gutter is FIXED (`river_gutter`, 6 = the width of `SELECT`) rather than
resolved from the widest keyword present. That is the whole reason this is
buildable: every clause handler computes its body column at layout time, so a
resolver-determined keyword width would leave those columns stale — the
detachment bug `subquery.py`'s docstring records. A constant keeps it
layout-time knowable, which is also why the river survives `align=False`.

Two things do not fit the gutter, and they are deliberately different:

- a **JOIN** goes to the far side (`gutter + 1`) whatever its width, which is
  Holywell's documented rule and is about joins rather than about long keywords;
- an over-long **root** keyword (`GROUP BY`, `ORDER BY` — 8 against a gutter of
  6) stays at the margin and overhangs. Sending those to the far side too would
  indent them past `WHERE`, reading as though they were subordinate to it.

The `river` preset is `align=False` on top, which is Holywell's own style: the
river is the only alignment his guide asks for.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import HOUSE, Style, preset_style

RIVER = preset_style("river")
PADDED = Style(clause_keyword_align="river", on_placement="own_line")


def fmt(sql, style=RIVER, dialect="postgres"):
    result = format_sql(sql, dialect, style)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- the published example ------------------------------------------------

def test_reproduces_holywells_example():
    """Transcribed from sqlstyle.guide's own JOIN example. His `AS` on the table
    aliases is `table_alias_style`, layered on here so the comparison is about
    the geometry rather than the spelling."""
    got = fmt("select r.last_name from riders r "
              "inner join bikes b on r.bike_vin_num = b.vin_num "
              "and b.engine_tally > 2;",
              preset_style("river", table_alias_style="as"))
    assert got == (
        "SELECT r.last_name\n"
        "  FROM riders AS r\n"
        "       INNER JOIN bikes AS b\n"
        "       ON r.bike_vin_num = b.vin_num\n"
        "          AND b.engine_tally > 2;"
    )


def test_every_fitting_keyword_ends_on_the_gutter():
    out = fmt("select a from t where a > 0 limit 10;")
    for line, keyword in ((0, "SELECT"), (1, "FROM"), (2, "WHERE"), (3, "LIMIT")):
        row = out.split("\n")[line]
        assert row.index(keyword) + len(keyword) == 6, f"{keyword!r} in {row!r}"


def test_over_long_root_keywords_overhang_rather_than_indent():
    """`GROUP BY` past `WHERE` would read as subordinate to it."""
    out = fmt("select a, b from t where a > 0 group by a, b order by a;")
    rows = out.split("\n")
    assert next(r for r in rows if "GROUP BY" in r).startswith("GROUP BY")
    assert next(r for r in rows if "ORDER BY" in r).startswith("ORDER BY")
    assert next(r for r in rows if "WHERE" in r).startswith(" WHERE")


def test_joins_hang_on_the_far_side_whatever_their_width():
    out = fmt("select 1 from a x join b y on y.id = x.id "
              "left join c z on z.id = x.id;")
    for keyword in ("JOIN b", "LEFT JOIN c"):
        row = next(r for r in out.split("\n") if keyword in r)
        assert row.index(keyword.split()[0]) == 7, row


def test_the_gutter_is_configurable():
    out = fmt("select a from t;", Style(clause_keyword_align="river", river_gutter=10))
    assert out == "    SELECT a\n      FROM t;"


# ---- it reaches every construct, not just a plain SELECT ------------------

@pytest.mark.parametrize("sql,expect", [
    ("with r as (select id from o) select id from r;", "\n  SELECT id\n    FROM o\n"),
    ("select a from t union all select a from u;", "SELECT a\n  FROM t\n"),
    ("update orders o set n = 1 where o.id = 2;", "UPDATE orders o\n   SET n = 1\n WHERE"),
    ("delete from orders o where o.id = 2;", "DELETE FROM orders o\n WHERE o.id = 2;"),
    ("select 1 from a x join (select q from z) d on d.q = x.q;", "\n       JOIN (SELECT q"),
])
def test_nested_and_dml_constructs_join_the_river(sql, expect):
    assert expect in fmt(sql), fmt(sql)


def test_a_cte_body_gets_its_own_river_at_its_own_indent():
    out = fmt("with recent as (select id from orders where d > '2020-01-01') "
              "select id from recent;")
    assert "WITH recent AS (\n  SELECT id\n    FROM orders\n   WHERE d > " in out, out


# ---- invariants -----------------------------------------------------------

def test_the_river_survives_align_off():
    """It is a layout-time indent, not a resolver tag — which is exactly why a
    fixed gutter was the right call."""
    plain = fmt("select a from t where a > 0;", Style(clause_keyword_align="river", align=False))
    assert plain == "SELECT a\n  FROM t\n WHERE a > 0;"


def test_reformatting_a_river_does_not_indent_it_further():
    """The formatter preserved each statement's leading trivia verbatim,
    including the horizontal indent before its first token. House output starts
    at column 0 so it never showed; a river's first line does not, so every pass
    added the gutter again. Caught by the knob-combination sweep, not by hand."""
    style = Style(clause_keyword_align="river", river_gutter=8, select_placement="own_line")
    once = fmt("select a, bb from t where x = 1;", style)
    assert fmt(once, style) == once
    assert once.startswith("  SELECT"), once


def test_a_leading_comment_still_survives_verbatim():
    """The indent is given back to the body; everything else in the prefix — a
    comment, a blank line — is still preserved."""
    out = fmt("-- keep me\n\nselect a from t;")
    # SELECT is exactly the gutter width, so it takes no padding; FROM does.
    assert out == "-- keep me\n\nSELECT a\n  FROM t;", out


def test_a_subquerys_paren_stays_against_its_keyword():
    """The inner SELECT carries its own river padding; the `(` takes that space
    rather than sitting in front of it."""
    out = fmt("select id, (select max(x) from z) as mx from t;",
              Style(clause_keyword_align="river", river_gutter=8))
    assert "(SELECT" in out and "(  SELECT" not in out, out


def test_house_is_unaffected():
    assert HOUSE.clause_keyword_align == "left"
    assert fmt("select a from t;", HOUSE) == "SELECT a\nFROM t;"


@pytest.mark.parametrize("style", [RIVER, PADDED])
@pytest.mark.parametrize("sid", SAMPLES)
def test_content_semantics_and_idempotence(sid, style):
    inp, expected = load_pair(sid)
    dialect = DIALECTS.get(sid, "postgres")
    result = format_sql(inp, dialect, style)
    if result.warnings:
        pytest.skip("declined under this style, which is a safe outcome")
    out = result.text
    assert out.split() == expected.split(), "tokens moved, not just whitespace"
    assert ast_equal(inp, out, dialect)
    assert format_sql(out, dialect, style).text == out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged_by_the_default(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


# ---- validation -----------------------------------------------------------

@pytest.mark.parametrize("bad", ["River", "right", "", True])
def test_rejects_bad_alignment(bad):
    with pytest.raises(ValueError):
        Style(clause_keyword_align=bad)


@pytest.mark.parametrize("bad", [1, 0, -6, "6", 6.5, True])
def test_rejects_bad_gutter(bad):
    with pytest.raises(ValueError):
        Style(river_gutter=bad)


def test_config_and_cli(tmp_path):
    from sqlalign.cli import main

    sql = tmp_path / "q.sql"
    sql.write_text("select a from t;\n")
    assert main(["--clause-keyword-align", "river", str(sql)]) == 0
    assert sql.read_text() == "SELECT a\n  FROM t;\n"

    (tmp_path / ".sqlalign.toml").write_text(
        'clause_keyword_align = "river"\nriver_gutter = 8\n')
    sql.write_text("select a from t;\n")
    assert main([str(sql)]) == 0
    assert sql.read_text() == "  SELECT a\n    FROM t;\n"
