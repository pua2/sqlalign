"""`Style.align = False` — the master alignment switch (`--no-align`).

9 of 10 published SQL style guides produce unpadded output, so a team that wants
sqlalign's line structure without its columnar look must be able to turn padding
off. Alignment is a pure emit-time concern: layout handlers decide line breaks
and indents first, and `align.render` pads afterwards, so switching it off must
change ONLY intra-line spacing — never structure, content, or meaning.

These invariants are asserted across every golden rather than by adding a second
full set of byte-exact fixtures, which is what keeps the option affordable.
"""
import re

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import COMPACT, HOUSE


def _squeeze(text: str) -> str:
    """Collapse every run of spaces to one — erases alignment padding AND the
    handlers' own baked-in spacing, leaving pure structure + content."""
    return "\n".join(re.sub(r" +", " ", line).rstrip() for line in text.split("\n"))


@pytest.mark.parametrize("sid", SAMPLES)
def test_align_off_changes_only_spacing(sid):
    """INV: unaligned output is the aligned output modulo horizontal whitespace.
    Catches any line break, token, or ordering difference introduced by the switch."""
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    aligned = format_sql(inp, dialect, HOUSE).text
    unaligned = format_sql(inp, dialect, COMPACT).text
    assert _squeeze(unaligned) == _squeeze(aligned)


@pytest.mark.parametrize("sid", SAMPLES)
def test_align_off_preserves_semantics(sid):
    """INV: the safety guarantee holds with alignment off."""
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    assert ast_equal(inp, format_sql(inp, dialect, COMPACT).text, dialect)


@pytest.mark.parametrize("sid", SAMPLES)
def test_align_off_is_idempotent(sid):
    """INV: formatting unaligned output again is a no-op."""
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    once = format_sql(inp, dialect, COMPACT).text
    assert format_sql(once, dialect, COMPACT).text == once


def test_align_off_removes_padding():
    """The switch actually does something: no run of 2+ spaces survives inside a
    line's content (leading indent excepted) for a fixture whose aligned form is
    full of padding."""
    unaligned = format_sql(load_pair("13")[0], "postgres", COMPACT).text
    for line in unaligned.split("\n"):
        assert "  " not in line.lstrip(" "), line


def test_align_off_keeps_join_continuation_indented():
    """A continuation AND must stay visibly attached to its JOIN rather than
    falling to column 0, where it would read as a new top-level clause. Its
    aligned position comes from the resolver, so the line carries a base indent
    that is correct on its own."""
    unaligned = format_sql(load_pair("13")[0], "postgres", COMPACT).text
    and_lines = [ln for ln in unaligned.split("\n") if ln.lstrip().startswith("AND ")]
    assert and_lines, "fixture 13 should have continuation AND lines"
    for line in and_lines:
        assert line.startswith("  "), f"continuation lost its indent: {line!r}"


def test_house_default_is_still_aligned():
    """Guard against the switch silently flipping: the default is unchanged."""
    assert HOUSE.align is True
    inp, expected = load_pair("13")
    assert format_sql(inp, "postgres").text == expected
