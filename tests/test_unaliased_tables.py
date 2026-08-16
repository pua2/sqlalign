"""A FROM/JOIN table without an alias.

`fromjoin.py` used to decline these outright, on the stated grounds that "the
alias/ON/op geometry is defined in terms of alias widths; an unaliased table
makes that undefined". It isn't: a row with no alias has nothing to put in the
alias column, so it simply does not participate in it, and the `on`/`op` columns
are right-aligned and land correctly either way.

That decline mattered — unaliased tables are ordinary SQL, and GitLab's style
guide (docs/sql-style-guide.md) actively prefers the full table name over an
alias, so nothing written in that style could be formatted at all.

The invariant these tests defend: a missing alias removes a row from one column
and changes nothing else. No stray padding, no shifted operator column, and
never a double space where a single one belongs.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style

TRAILING_BOOLS = Style(align=False, boolean_operator_position="trailing")


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- the decline is gone --------------------------------------------------

@pytest.mark.parametrize("sql", [
    "select 1 from a join b on b.id = a.id;",              # neither aliased
    "select 1 from a x join b on b.id = x.id;",            # joined table bare
    "select 1 from a join b y on y.id = a.id;",            # base table bare
    "select 1 from a x join b y on y.id = x.id;",          # both aliased (regression)
])
def test_unaliased_tables_format(sql):
    out = fmt(sql)
    assert out != sql, "still passing through"
    assert ast_equal(sql, out, "postgres")


def test_no_alias_anywhere():
    assert fmt("select 1 from a join b on b.id = a.id;") == (
        "SELECT 1\n"
        "FROM a\n"
        "JOIN b ON b.id = a.id;"
    )


def test_mixed_aliased_and_bare_keeps_one_operator_column():
    """The bare row drops out of the alias column, but the block-global ON and
    operator columns still span every join."""
    out = fmt(
        "select 1 from customers cust "
        "join orders ord on ord.customer_id = cust.id "
        "join date_details on date_details.d = ord.d;"
    )
    lines = out.rstrip("\n").split("\n")
    joins = [ln for ln in lines if " ON " in ln]
    assert len({ln.index(" ON ") for ln in joins}) == 1, f"ON column split:\n{out}"
    assert len({ln.index(" = ") for ln in joins}) == 1, f"operator column split:\n{out}"


def test_bare_table_row_has_no_phantom_padding():
    """Nothing is aligned into an alias column the row does not participate in."""
    out = fmt("select 1 from a join b on b.id = a.id;")
    assert "  " not in out, f"unexpected padding:\n{out}"


# ---- the empty-segment phantom space (pre-existing bug) -------------------

def test_unaligned_trailing_booleans_indent_by_two():
    """`align=False` with trailing booleans emitted a THREE-space continuation
    indent: fromjoin.py heads that row with an empty Seg to hold the ON column,
    and render's unaligned path joined it with a space like any other segment.
    The continuation indent is 2."""
    out = fmt("select 1 from a x join b y on y.id = x.id and y.k = 'z';", TRAILING_BOOLS)
    cont = [ln for ln in out.split("\n") if ln.lstrip().startswith("y.k")]
    assert cont == ["  y.k = 'z';"], f"wrong continuation indent: {cont!r}\n{out}"


def test_unaligned_output_never_has_a_double_space():
    """With alignment off there is no padding, so two spaces in a row can only
    come from an empty segment leaking its separator."""
    style = Style(align=False)
    for sql in (
        "select 1 from a join b on b.id = a.id;",
        "select 1 from customers cust join orders on orders.cid = cust.id;",
        "select 1 from a x join b y on y.id = x.id and y.k = 'z';",
    ):
        out = fmt(sql, style)
        assert "  " not in out.replace("\n  ", "\n"), f"double space in:\n{out}"


# ---- the fix does not disturb what already worked -------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


def test_idempotent():
    once = fmt("select 1 from customers cust join orders on orders.cid = cust.id;")
    assert fmt(once) == once
