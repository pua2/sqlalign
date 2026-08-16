"""Output-spelling and scope knobs on `Style`.

`neq_style` and `decimal_style` exist because sqlglot's parser DESTROYS those two
distinctions (`<>`/`!=` and DECIMAL/NUMERIC both collapse to a single AST node), so
sqlalign must choose a spelling when printing. Everything sqlglot preserves — cast
form, GROUP BY references, alias presence — is passed through untouched and is
deliberately NOT configurable; unifying those is lint, not formatting.

`format_dollar_bodies` is a SCOPE switch (whether to touch a region), not a style
choice.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import HOUSE, Style

# ---- neq_style -------------------------------------------------------------

def test_neq_defaults_to_bang_equals():
    out = format_sql("select a from t where x <> 1;", "postgres").text
    assert "!=" in out and "<>" not in out


def test_neq_style_angle_brackets():
    style = Style(neq_style="<>")
    out = format_sql("select a from t where x != 1;", "postgres", style).text
    assert "<>" in out and "!=" not in out


def test_neq_style_applies_outside_laid_out_clauses():
    """render_expr's path (a select-list expression) reads the same knob as
    conditions.py's laid-out WHERE path — otherwise one file would say `!=` and
    the other `<>`."""
    sql = "select (a <> b) as differs from t where c <> 1;"
    out = format_sql(sql, "postgres", Style(neq_style="<>")).text
    assert out.count("<>") == 2
    assert "!=" not in out


def test_neq_style_is_semantics_preserving():
    sql = "select a from t where x <> 1;"
    for style in (HOUSE, Style(neq_style="<>")):
        assert ast_equal(sql, format_sql(sql, "postgres", style).text, "postgres")


# ---- decimal_style ---------------------------------------------------------

def test_decimal_defaults_to_numeric():
    out = format_sql("select cast(x as decimal) from t;", "postgres").text
    assert "NUMERIC" in out and "DECIMAL" not in out


def test_decimal_style_decimal():
    out = format_sql("select cast(x as numeric) from t;", "postgres",
                     Style(decimal_style="DECIMAL")).text
    assert "DECIMAL" in out and "NUMERIC" not in out


def test_decimal_style_keeps_precision_args():
    out = format_sql("select cast(x as numeric(10, 2)) from t;", "postgres",
                     Style(decimal_style="DECIMAL")).text
    assert "DECIMAL(10, 2)" in out


# ---- validation ------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"neq_style": "!=="}, {"neq_style": "ne"}, {"decimal_style": "numeric"},
    {"decimal_style": "FLOAT"},
])
def test_invalid_knob_values_are_rejected(kwargs):
    with pytest.raises(ValueError):
        Style(**kwargs)


# ---- format_dollar_bodies --------------------------------------------------

def test_dollar_bodies_formatted_by_default():
    inp, expected = load_pair("19")
    assert format_sql(inp, "postgres").text == expected


def test_no_format_bodies_passes_procedure_through_byte_identical():
    inp = load_pair("19")[0]
    result = format_sql(inp, "postgres", Style(format_dollar_bodies=False))
    assert result.text == inp
    assert result.warnings == []      # a user choice, not a decline


def test_no_format_bodies_still_formats_surrounding_statements():
    """The switch is per-region, not per-file: ordinary statements beside a
    procedure still format."""
    inp = "select a,b from t;\n\n" + load_pair("20")[0].split("\n", 1)[1]
    result = format_sql(inp, "postgres", Style(format_dollar_bodies=False))
    assert result.text.startswith("SELECT a\n     , b\nFROM t;")
    assert "create procedure" in result.text     # body untouched, still lowercase


# ---- knobs do not disturb the house defaults -------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_defaults_reproduce_the_goldens(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres"), HOUSE).text == expected
