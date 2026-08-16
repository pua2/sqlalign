"""Property tests over COMBINATIONS of style knobs.

Every knob so far has per-feature tests, and a few pairwise compositions are
asserted by hand. But the combination space is now far larger than the geometry
tests that pin it, and the defects found while building these options were all
cross-cutting rather than local (a continuation row that collapsed to column 0
only when alignment was off; a comma site missed in one module; a boolean site
missed in another). Those are exactly the failures a per-feature test cannot see.

So instead of enumerating combinations byte-for-byte -- which would need a fixture
per combination and is the reason "just add more knobs" usually rots a formatter --
this asserts the properties that must hold for EVERY combination:

  1. semantics never change      (the safety net's own guarantee)
  2. output is idempotent        (running the formatter twice is a no-op)
  3. output re-parses            (we never emit unparseable SQL)
  4. no trailing whitespace      (a formatter must not leave debris)
  5. layout knobs move tokens, they never add, drop, or reorder them

Combinations are drawn from a FIXED seed, so a failure is reproducible rather
than a flake.
"""
import random
import re

import pytest
import sqlglot
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import ALL_ALIGN_TARGETS, HOUSE_ALIGN_TARGETS, Style

SEED = 20260810

# Knobs that only move things around. `neq_style`/`decimal_style` are deliberately
# excluded: they change the printed TOKENS (`!=` vs `<>`), so they cannot satisfy
# property 5 and have their own tests. `format_dollar_bodies` is excluded for the
# same reason -- switching it off leaves a procedure body unformatted.
LAYOUT_KNOBS = {
    "align": [True, False],
    "comma_position": ["leading", "trailing"],
    "boolean_operator_position": ["leading", "trailing"],
    "on_placement": ["inline", "own_line"],
    "select_placement": ["inline", "own_line"],
    "select_indent": [4, 2, 6],
    "clause_keyword_align": ["left", "river"],
    "river_gutter": [6, 8],
    "align_targets": [
        HOUSE_ALIGN_TARGETS,          # v[0] must be the house default
        ALL_ALIGN_TARGETS,            # every target, incl. opt-in table_names
        frozenset(),
        frozenset({"aliases"}),
        frozenset({"operators", "join_conditions"}),
        ALL_ALIGN_TARGETS - {"join_conditions"},
    ],
}


def _combinations(n):
    rng = random.Random(SEED)
    out = [{k: v[0] for k, v in LAYOUT_KNOBS.items()}]          # the house defaults
    out.append({k: v[1] for k, v in LAYOUT_KNOBS.items()})      # everything flipped
    while len(out) < n:
        out.append({k: rng.choice(v) for k, v in LAYOUT_KNOBS.items()})
    return out


COMBOS = _combinations(10)
# Name each combination by the two knobs that read clearly in a test id; the index
# keeps ids unique when two combinations agree on both.
COMBO_IDS = [f"align={c['align']}+comma_position={c['comma_position']}#{i}"
             for i, c in enumerate(COMBOS)]


def _tokens(text: str) -> str:
    """Content with all layout information erased: whitespace collapsed, and
    whitespace around commas removed so a leading comma and a trailing comma
    reduce to the same string. What remains is the token stream, which no layout
    knob may alter."""
    return re.sub(r"\s*,\s*", ",", re.sub(r"\s+", " ", text)).strip()


@pytest.mark.parametrize("combo", COMBOS, ids=COMBO_IDS)
@pytest.mark.parametrize("sid", SAMPLES)
def test_every_combination_holds_the_invariants(sid, combo):
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    style = Style(**combo)
    out = format_sql(inp, dialect, style).text

    # 1. semantics preserved
    assert ast_equal(inp, out, dialect), "combination changed the meaning"

    # 2. idempotent
    assert format_sql(out, dialect, style).text == out, "second pass changed the output"

    # 3. parses
    sqlglot.parse(out, read=dialect)

    # 4. no trailing whitespace
    for line in out.split("\n"):
        assert line == line.rstrip(), repr(line)

    # 5. same tokens as the house rendering — layout knobs move, never mutate
    house = format_sql(inp, dialect, Style()).text
    assert _tokens(out) == _tokens(house)


def test_the_combination_space_is_not_degenerate():
    """A property suite that exercises ONE behaviour ten times proves nothing.
    The sampled combinations must actually produce different formattings, or the
    parametrization above is theatre."""
    inp = load_pair("13")[0]
    outs = {format_sql(inp, "postgres", Style(**c)).text for c in COMBOS}
    assert len(outs) >= len(COMBOS) - 2, "combinations collapse to the same output"


def test_the_token_invariant_has_teeth():
    """The invariant in property 5 must FAIL when tokens are dropped or
    reordered. Asserted explicitly because an invariant that cannot fail is
    indistinguishable from one that passes."""
    house = format_sql(load_pair("13")[0], "postgres").text
    dropped = house.replace("     , cust.email\n", "")
    assert dropped != house and _tokens(dropped) != _tokens(house)
    reordered = (house.replace("cust.customer_id", "\0")
                      .replace("cust.email", "cust.customer_id")
                      .replace("\0", "cust.email"))
    assert reordered != house and _tokens(reordered) != _tokens(house)


@pytest.mark.parametrize("sid", ["13", "08", "06", "11"])
def test_combinations_are_stable_across_repeated_runs(sid):
    """The same style must produce the same bytes every time — no dependence on
    dict ordering, id() values, or the ambient style leaking between calls."""
    inp = load_pair(sid)[0]
    for combo in COMBOS:
        style = Style(**combo)
        first = format_sql(inp, "postgres", style).text
        for _ in range(3):
            assert format_sql(inp, "postgres", style).text == first


def test_ambient_style_does_not_leak_between_calls():
    """Output-spelling knobs ride a contextvar; a call with a non-default style
    must not affect the next call's defaults."""
    sql = "select a from t where x <> 1;"
    format_sql(sql, "postgres", Style(neq_style="<>"))
    assert "!=" in format_sql(sql, "postgres").text


def test_defaults_are_still_the_house_style():
    """A guard on the whole knob surface: adding a knob must not quietly change
    what sqlalign does when nobody configures it."""
    for sid in SAMPLES:
        inp, expected = load_pair(sid)
        assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected
