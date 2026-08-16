"""Defense-in-depth + comment/skip robustness (panel task 7 fixes 1, 2, 6).

The overarching invariant: **no single statement can abort the whole file**.
Whatever a statement does — an unsupported construct, a would-change-semantics
render, or an outright internal bug — the file still formats, its healthy
siblings still format, and the offending statement is passed through
byte-identical with a warning on stderr. `format_sql` never raises for a
statement-level problem.
"""
import pytest

from sqlalign.formatter import format_sql

# (input, substring the emitted warning must contain). Each of these is a
# construct outside the proven subset; every one must be a byte-identical
# passthrough + >=1 warning, and must NOT raise. The wording is load-bearing (and
# asserted on by later test files): "unsupported construct" = an expected decline;
# "would change semantics" = the safety net firing; "internal formatter error" =
# a real bug surfaced (tested separately below).
# Prefer constructs whose decline is PERMANENT over ones that are merely not
# implemented yet — an entry that starts formatting turns this panel red for the
# wrong reason, which GROUPING SETS/ROLLUP/CUBE all did when they landed.
PANEL = [
    ("SELECT * FROM t UNPIVOT (x FOR y IN (a, b)) u;\n", "unsupported construct"),
    ("SELECT * FROM t PIVOT (sum(x) FOR y IN (1, 2)) p;\n", "unsupported construct"),
]
# `SELECT a FROM t FOR UPDATE` used to sit here and now formats -- the panel's
# own warning about preferring PERMANENT declines, earned a third time.
#
# `WHERE x NOT LIKE '%z%'` used to sit here as the "formatting would change
# semantics" entry, and that was the panel documenting a BUG as though it were
# expected behaviour. `exp.Like` carries negation as a FLAG rather than a
# wrapping `Not`, the flag was never read, so the renderer emitted plain `LIKE`
# and only the re-parse guard stopped it shipping. A decline with that wording
# is never expected; tests/test_no_silent_declines.py now sweeps for it.


@pytest.mark.parametrize("src,expected", PANEL)
def test_panel_construct_passes_through_without_aborting(src, expected):
    result = format_sql(src)                 # must not raise
    assert result.text == src                # byte-identical passthrough
    assert len(result.warnings) >= 1
    assert any(expected in w for w in result.warnings)


def test_middle_statement_failure_does_not_abort_file():
    """Only the middle statement is unsupported; the two healthy siblings must
    STILL format and the file must not abort (this is the whole point of FIX 1)."""
    src = ("select x, y from a;\n"
           "SELECT * FROM t PIVOT (sum(x) FOR y IN (1, 2)) p;\n"
           "select p, q from b;\n")
    result = format_sql(src)                 # must not raise
    assert "SELECT * FROM t PIVOT (sum(x) FOR y IN (1, 2)) p;" in result.text  # verbatim
    assert "SELECT x\n     , y\nFROM a;" in result.text          # sibling 1 formatted
    assert "SELECT p\n     , q\nFROM b;" in result.text          # sibling 2 formatted
    assert len(result.warnings) >= 1


def test_internal_error_is_caught_and_reported_distinctly(monkeypatch):
    """A genuine bug in a per-statement layout must be caught, reported with the
    distinct 'internal formatter error' wording (so it is visible, not hidden),
    and still produce a byte-identical passthrough of that statement."""
    import sqlalign.formatter as fmt

    def boom(stmt, dialect, width):
        raise ValueError("kaboom")

    monkeypatch.setattr(fmt, "_format_statement", boom)
    src = "SELECT 1;\n"
    result = fmt.format_sql(src)             # must not raise
    assert result.text == src
    assert any("internal formatter error" in w and "ValueError" in w
               for w in result.warnings)


# --- FIX 2: trailing same-line comment after ';' must not break formatting ----

def test_trailing_line_comment_after_semicolon_formats_body():
    result = format_sql("select a,b from t; -- note\n")
    assert result.text == "SELECT a\n     , b\nFROM t; -- note\n"
    assert result.warnings == []             # body FORMATTED, not passed through


def test_trailing_block_comment_after_semicolon_formats_body():
    result = format_sql("select a,b from t; /* blk */\n")
    assert result.text == "SELECT a\n     , b\nFROM t; /* blk */\n"
    assert result.warnings == []


# --- FIX 6: trailing `-- sqlalign: skip` recognized ---------------------------

def test_trailing_skip_directive_passes_through():
    src = "SELECT bad syntax here; -- sqlalign: skip\n"
    result = format_sql(src)
    assert result.text == src                # byte-identical, no parse attempt
    assert result.warnings == []             # no error, no warning
