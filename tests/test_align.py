from sqlalign.align import render
from sqlalign.ir import Line, Seg
from sqlalign.style import HOUSE


def test_left_alignment_pads_to_longest():
    lines = [
        Line(0, [Seg("SELECT status"), Seg("AS s", scope="sel", kind="as")]),
        Line(0, [Seg("     , created_at"), Seg("AS c", scope="sel", kind="as")]),
    ]
    assert render(lines) == "SELECT status     AS s\n     , created_at AS c\n"


def test_operators_right_align_to_common_end():
    # #1 WHERE: '=' and '>=' both end at the same column
    lines = [
        Line(0, [Seg("WHERE status"), Seg("=", scope="w", kind="op"), Seg("'active'")]),
        Line(0, [Seg("  AND created_at"), Seg(">=", scope="w", kind="op"), Seg("'2026-01-01'")]),
    ]
    assert render(lines) == (
        "WHERE status      = 'active'\n"
        "  AND created_at >= '2026-01-01'\n"
    )


def test_scopes_do_not_interfere():
    lines = [
        Line(0, [Seg("a"), Seg("=", scope="s1", kind="op"), Seg("1")]),
        Line(0, [Seg("longer_name"), Seg("=", scope="s2", kind="op"), Seg("2")]),
    ]
    assert render(lines) == "a = 1\nlonger_name = 2\n"


def test_unscoped_segments_join_with_single_space():
    assert render([Line(2, [Seg("SELECT"), Seg("1")])]) == "  SELECT 1\n"


def test_an_empty_untagged_segment_contributes_neither_text_nor_separator():
    """It renders nothing and holds no column, so it must not push the next
    segment along by a space. `apply_align_targets` manufactures these by
    untagging a column-holding segment when its target is switched off."""
    line = Line(2, [Seg("SELECT"), Seg(""), Seg("1")])
    assert render([line]) == "  SELECT 1\n"
    assert render([line], align=False) == "  SELECT 1\n"


def test_an_empty_TAGGED_segment_still_holds_its_column():
    """The deliberate case: a handler emits one to reserve a column when this
    row has nothing to put in it (fromjoin.py's trailing-boolean ON head)."""
    lines = [
        Line(0, [Seg("a"), Seg("ON", scope="s", kind="on"), Seg("1")]),
        Line(0, [Seg("longer"), Seg("", scope="s", kind="on"), Seg("2")]),
    ]
    # The ON column ends at 7, driven by row 2: `longer` + separator + a
    # zero-width segment. Row 1 pads `ON` out to meet it.
    assert render(lines) == "a    ON 1\nlonger  2\n"


def test_house_width_limit_at_column_zero():
    # 100 target + 5 grace (the floor only binds for an indented anchor).
    assert HOUSE.width.limit(0) == 105


# --- Dependency-aware composition (two chained tagged columns on one line) ---

def test_composes_dependent_columns_on_same_line():
    # `alias` (left-aligned) then `on` (right-aligned) then `op` (right-aligned)
    # all share the two rows. The LONGEST prefix is on row 1 but the LONGEST
    # alias is on row 2, so the alias column is driven by row 1 while the `on`
    # column (which sits after the alias) is driven by row 2. A pass-1 that
    # ignored the row-2 alias being padded rightward would under-place `on`
    # (and `op` after it) on row 1 and the columns would disagree.
    lines = [
        Line(0, [Seg("FROM big_table"), Seg("a", scope="s", kind="alias"),
                 Seg("ON", scope="s", kind="on"),
                 Seg("a.id"), Seg("=", scope="s", kind="op"), Seg("b.id")]),
        Line(0, [Seg("JOIN t"), Seg("wider_alias", scope="s", kind="alias"),
                 Seg("ON", scope="s", kind="on"),
                 Seg("t.id"), Seg("=", scope="s", kind="op"), Seg("b.id")]),
    ]
    out = render(lines).splitlines()
    assert out[0].index("ON") == out[1].index("ON")     # `on` columns agree
    assert out[0].index("=") == out[1].index("=")       # `op` columns agree


def test_right_aligned_op_with_longest_lhs_on_continuation():
    # The exact shipping bug: an ON row followed by a continuation AND row whose
    # LHS is longer. AND right-aligns to end at the ON column (both kind="on"),
    # which pushes the continuation's LHS — and therefore its operator — rightward.
    # Both operators must end at ONE common column, driven by the (padded)
    # continuation row, even though they have different widths ('=' vs '!=').
    lines = [
        Line(0, [Seg("JOIN t x"), Seg("ON", scope="s", kind="on"),
                 Seg("x.id"), Seg("=", scope="s", kind="op"), Seg("o.id")]),
        Line(0, [Seg("AND", scope="s", kind="on"),
                 Seg("x.longer_col"), Seg("!=", scope="s", kind="op"), Seg("o.type")]),
    ]
    out = render(lines).splitlines()
    end0 = out[0].index("=") + len("=")
    end1 = out[1].index("!=") + len("!=")
    assert end0 == end1                                  # ops end at one column


def test_no_dependent_columns_output_unchanged():
    # Regression guard: a case with at most one tagged column per line (no
    # composition) must render exactly as it did before the fixpoint change —
    # byte-identical to test_operators_right_align_to_common_end.
    lines = [
        Line(0, [Seg("WHERE status"), Seg("=", scope="w", kind="op"), Seg("'active'")]),
        Line(0, [Seg("  AND created_at"), Seg(">=", scope="w", kind="op"), Seg("'2026-01-01'")]),
    ]
    assert render(lines) == (
        "WHERE status      = 'active'\n"
        "  AND created_at >= '2026-01-01'\n"
    )
