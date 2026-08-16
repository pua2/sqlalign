"""Two settings that were hardcoded: `width = 0` (wrapping off) and
`body_blank_lines`.

`width` was already plumbed everywhere as `config.Width`; what was missing was a
way to say "none of the above". Published guides disagree hard on the number
(50/80/88/100/120), which is exactly why some of them decline to have one —
GitLab's is `off`. The sentinel is a value of `width` rather than a separate
boolean because they are the same decision: a team that does not wrap has no
width to state.

`body_blank_lines` governs the vertical rhythm INSIDE a dollar-quoted body.
`blank_lines_between_statements` never reached in there, so the body's single
blank line was a literal that no setting could move.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.config import Width
from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import HOUSE, Style

OFF = Style(width=Width(width=0))

BODY = ("create function f() returns int language plpgsql as $$\n"
        "declare v int;\nbegin\nselect 1 into v;\nreturn v;\nend;\n$$;")


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- width = 0 -----------------------------------------------------------

def test_width_off_stops_a_length_break():
    """Sample 09's window frame wraps at the default width and must not here."""
    inp = load_pair("09")[0]
    assert "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)" in fmt(inp)
    wrapped = [ln for ln in fmt(inp).split("\n") if ln.strip().startswith("ROWS BETWEEN")]
    assert wrapped, "the sample stopped wrapping; pick another to test the sentinel"
    assert not [ln for ln in fmt(inp, OFF).split("\n") if ln.strip().startswith("ROWS BETWEEN")]


def test_width_off_keeps_structural_breaks():
    """Only LENGTH-driven breaks go away. One select item per line, one clause
    per line, are structure — not a width decision."""
    out = fmt("select a, b, c from t where x = 1;", OFF)
    assert out == "SELECT a\n     , b\n     , c\nFROM t\nWHERE x = 1;"


def test_the_limit_is_effectively_infinite():
    assert Width(width=0).limit(0) > 10 ** 6
    assert Width(width=0).limit(500) > 10 ** 6


def test_a_normal_width_is_unaffected():
    assert Width().limit(0) == 105                # 100 + grace
    assert Width(width=80).limit(0) == 85


@pytest.mark.parametrize("sid", SAMPLES)
def test_width_off_never_changes_meaning(sid):
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    result = format_sql(inp, dialect, OFF)
    if result.warnings:
        pytest.skip("declined under this style, which is a safe outcome")
    assert ast_equal(inp, result.text, dialect)
    assert format_sql(result.text, dialect, OFF).text == result.text


# ---- body_blank_lines ----------------------------------------------------

def test_default_is_one_blank_line():
    assert HOUSE.body_blank_lines == 1
    assert "DECLARE v INT;\n\nBEGIN\n\nSELECT 1 INTO v;" in fmt(BODY)


def test_zero_packs_the_body():
    assert "DECLARE v INT;\nBEGIN\nSELECT 1 INTO v;\nRETURN v;\nEND;" in fmt(
        BODY, Style(body_blank_lines=0))


def test_two_doubles_the_gaps():
    assert "DECLARE v INT;\n\n\nBEGIN\n\n\nSELECT 1 INTO v;" in fmt(
        BODY, Style(body_blank_lines=2))


def test_it_is_independent_of_the_between_statements_knob():
    """The two govern different gaps; setting one must not move the other."""
    packed_body = fmt(BODY, Style(body_blank_lines=0))
    assert packed_body == fmt(BODY, Style(body_blank_lines=0,
                                          blank_lines_between_statements=2))


def test_the_between_statements_knob_never_reached_the_body():
    """Documents why this knob had to exist at all."""
    assert fmt(BODY, Style(blank_lines_between_statements=0)) == fmt(BODY)


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_body_stays_semantically_identical(n):
    out = fmt(BODY, Style(body_blank_lines=n))
    assert ast_equal(BODY, out, "postgres")
    assert fmt(out, Style(body_blank_lines=n)) == out


# ---- defaults and validation --------------------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged_by_the_defaults(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


@pytest.mark.parametrize("bad", [-1, "1", 1.5, True, None])
def test_body_blank_lines_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        Style(body_blank_lines=bad)


def test_config_and_cli(tmp_path):
    from sqlalign.cli import main

    sql = tmp_path / "q.sql"
    sql.write_text(BODY + "\n")
    assert main(["--body-blank-lines", "0", str(sql)]) == 0
    assert "DECLARE v INT;\nBEGIN" in sql.read_text()

    (tmp_path / ".sqlalign.toml").write_text("width = 0\nbody_blank_lines = 2\n")
    sql.write_text(BODY + "\n")
    assert main([str(sql)]) == 0
    assert "DECLARE v INT;\n\n\nBEGIN" in sql.read_text()
