"""Regression tests for the FROM/JOIN block, guarding the operator-alignment
composition bug fixed by the dependency-aware alignment resolver.

Before the fix, align.py's single-sweep resolver scored the block-global `op`
(comparison-operator) column from raw single-space positions, ignoring that a
continuation `AND` gets right-aligned rightward under `ON`. So a multi-condition
join whose LONGEST ON-LHS sat on a continuation line silently misaligned the two
operators: the `op` target was pinned by the shorter first-line LHS, and the
continuation operator overran it. See tests/test_align.py for the unit-level
proof; this is the end-to-end guard through format_sql.
"""
from sqlalign.formatter import format_sql


def test_multi_condition_join_longest_lhs_on_continuation():
    # The longest ON-LHS (`x.longer_discriminator`, 22 cols) is on the AND
    # continuation line, not the ON line (`x.id`, 4 cols) — the exact shipping
    # bug. (The FROM table is aliased `a t`. An unaliased table no longer
    # declines -- it just does not participate in the alias column -- so this
    # geometry holds either way; see tests/test_unaliased_tables.py.)
    q = "select a.id from a t join b x on x.id = t.id and x.longer_discriminator = t.type;"
    got = format_sql(q, "postgres").text

    # Expected columns derived character-by-character from (0-based):
    #   alias column   = longest prefix ("FROM a"/"JOIN b" = 6) + 1        -> col 7
    #   on END column  = longest alias (1, ends col 8) + 1 space + len(ON) -> col 11
    #   op END column  = max(lhs_end + 1 + op_width) over both conditions:
    #                    ON row : "x.id" ends 16 -> 16 + 1 + 1 = 18
    #                    AND row: AND right-aligned to end 11, then one space,
    #                             "x.longer_discriminator" ends 34 -> 34+1+1 = 36
    #                    => block-global op end column = max(18, 36) = 36
    #   so both '=' occupy col 35, and the continuation AND ends at the ON column.
    expected = (
        "SELECT a.id\n"
        "FROM a t\n"
        "JOIN b x ON x.id" + " " * 19 + "= t.id\n"
        + " " * 8 + "AND x.longer_discriminator = t.type;"
    )
    assert got == expected

    # Invariants, asserted independently of the literal above:
    lines = got.splitlines()
    on_line, and_line = lines[2], lines[3]
    # every ON-block '=' aligns to one block-global end column
    assert on_line.index("=") == and_line.index("=") == 35
    # the continuation AND right-aligns to end at the ON column
    assert on_line.index("ON") + len("ON") == and_line.index("AND") + len("AND") == 11
