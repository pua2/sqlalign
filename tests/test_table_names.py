"""The `table_names` alignment target — pad the FROM/JOIN keyword so the table
names share a column.

    house                       table_names
    FROM customers      cust    FROM       customers cust
    LEFT JOIN orders    ord     LEFT JOIN  orders    ord
    INNER JOIN payments pay     INNER JOIN payments  pay

The house style pads AFTER the table name to line the aliases up, which leaves
the names themselves ragged. This is the other published convention: pad after
the KEYWORD instead, so the eye tracks the table list. Both columns can be on at
once — the fixpoint resolver composes them, and the alias column is simply
measured from the padded names.

It is the one target `align` alone does not enable. That is deliberate: it
changes the shape of the FROM block rather than refining it, and every golden
would move. See `HOUSE_ALIGN_TARGETS`.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import HOUSE, HOUSE_ALIGN_TARGETS, Style

ON = Style(align_targets=HOUSE_ALIGN_TARGETS | {"table_names"})
ONLY = Style(align_targets=frozenset({"table_names"}))

BLOCK = ("select 1 from customers cust "
         "left join orders ord on ord.cid = cust.id "
         "inner join payments pay on pay.oid = ord.id;")


def fmt(sql, style, dialect="postgres"):
    result = format_sql(sql, dialect, style)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


def table_rows(text):
    return [ln.split(" ON ")[0] for ln in text.split("\n")
            if ln.startswith(("FROM ", "LEFT ", "INNER ", "RIGHT ", "FULL ", "JOIN "))]


# ---- the column exists, and only when asked for --------------------------

def test_table_names_share_a_column():
    rows = table_rows(fmt(BLOCK, ON))
    starts = {ln.index(ln.split()[-2]) for ln in rows}   # the table name, before its alias
    assert len(starts) == 1, "table names not aligned:\n" + "\n".join(rows)


def test_house_leaves_the_names_ragged():
    rows = table_rows(fmt(BLOCK, HOUSE))
    starts = {ln.index(ln.split()[-2]) for ln in rows}
    assert len(starts) > 1, "house should not align table names"


def test_exact_geometry():
    """Pinned byte-exact — this block is the example in README.md, and a
    hand-written example that drifts from real output is worse than none."""
    assert table_rows(fmt(BLOCK, ON)) == [
        "FROM       customers cust",
        "LEFT JOIN  orders    ord",
        "INNER JOIN payments  pay",
    ]
    assert table_rows(fmt(BLOCK, HOUSE)) == [
        "FROM customers      cust",
        "LEFT JOIN orders    ord",
        "INNER JOIN payments pay",
    ]


def test_readme_example_matches_real_output():
    """The README draws both spellings side by side. Parse its block back out
    and check each half against the formatter."""
    import pathlib
    import re
    readme = (pathlib.Path(__file__).parent.parent / "README.md").read_text()
    block = re.search(r"(-- house.*?)\n```", readme, re.S).group(1)
    header, *rows = block.rstrip("\n").split("\n")
    # Split at a fixed column, not on runs of spaces -- the left half is itself
    # column-padded, so a whitespace split would cut it in the middle.
    split_at = header.index("-- with table_names")
    left = [r[:split_at].rstrip() for r in rows]
    right = [r[split_at:].rstrip() for r in rows]
    assert left == table_rows(fmt(BLOCK, HOUSE))
    assert right == table_rows(fmt(BLOCK, ON))


# ---- composition ---------------------------------------------------------

def test_alias_column_survives_alongside_it():
    """Both columns at once: the names align, and the aliases align after them."""
    rows = table_rows(fmt(BLOCK, ON))
    assert len({ln.rindex(" ") for ln in rows}) == 1


def test_alone_it_drops_the_alias_column():
    """Only the names align; each alias follows its own name by one space."""
    rows = table_rows(fmt(BLOCK, ONLY))
    assert rows == ["FROM       customers cust",
                    "LEFT JOIN  orders ord",
                    "INNER JOIN payments pay"]


def test_single_table_from_joins_the_same_column():
    """A FROM with no joins is laid out by a different function (select.py's
    `_from_lines`, not fromjoin.py); it must honour the target too."""
    out = fmt("select 1 from customers cust;", ON)
    assert "FROM customers cust" in out


def test_unaliased_tables_still_align():
    out = fmt("select 1 from customers left join orders on orders.cid = customers.id;", ON)
    rows = table_rows(out)
    assert rows == ["FROM      customers", "LEFT JOIN orders"]


def test_a_derived_table_join_stays_out_of_the_column():
    """Spec §3.2 excludes a derived-table join from the FROM block's shared
    scopes — its alias and ON already sit outside them, and the table column is
    no different. Previously nothing was padded before the table name, so the
    exclusion was invisible; now it shows, which makes it worth pinning."""
    out = fmt("select 1 from apple a left join banana b on b.id = a.id "
              "join (select x from q) d on d.x = a.x;", ON)
    assert "\nFROM      apple  a" in out
    assert "\nLEFT JOIN banana b" in out
    assert "\nJOIN (SELECT x" in out          # not padded into the column above


def test_align_off_beats_the_target():
    """`align=False` means no padding at all, whatever the target list says."""
    out = fmt(BLOCK, Style(align=False, align_targets=HOUSE_ALIGN_TARGETS | {"table_names"}))
    assert "  " not in out


@pytest.mark.parametrize("style", [ON, ONLY])
def test_semantics_and_idempotence(style):
    out = fmt(BLOCK, style)
    assert ast_equal(BLOCK, out, "postgres")
    assert fmt(out, style) == out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_move_only_where_a_from_block_has_joins(sid):
    """Turning the target on must change nothing but the FROM block, and must
    never change what the statement means."""
    inp, expected = load_pair(sid)
    dialect = DIALECTS.get(sid, "postgres")
    out = fmt(inp, ON, dialect)
    assert ast_equal(inp, out, dialect)
    assert out.split() == expected.split(), "content changed, not just padding"
