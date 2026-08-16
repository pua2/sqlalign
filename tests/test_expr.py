"""Unit tests for the nested-CASE break (expr.py) 

Sample 21's select item `COALESCE(SUM(CASE … END), 0.00) AS funding_amt` is
over-width and breaks AT the inner CASE: the wrapper prefix `COALESCE(SUM(`
rides line 0, the CASE lays out long-form at shifted columns, and `END` sits at
CASE-col+1 with the wrapper's closers `), 0.00)` tucked onto the END line. The
handler is restricted to that shape and declines (Unsupported/None) anything
else; ast_equal guards the tokens, the golden guards the geometry.
"""
import sqlglot
from conftest import load_pair
from sqlglot import exp

from sqlalign.casing import parse_dialect
from sqlalign.config import Width
from sqlalign.formatter import ast_equal, format_sql
from sqlalign.layout import expr as E


def _second_item(body):
    """The select item under test: every body below puts it second, so the first
    item is only there to give the list a sibling."""
    node = next(e for e in sqlglot.parse(body, read=parse_dialect("postgres")) if e is not None)
    return node.expressions[1]


_SAMPLE21 = ("select total, coalesce(sum(case when ledger_event_type > 0.00 "
             "and ledger_event_type < 10.00 then ledger_event_type end), 0.00) "
             "as funding_amt from orders")


# ---- shape recognition -----------------------------------------------------

def test_matches_sample21_nested_case():
    item = _second_item(_SAMPLE21)
    case = E.nested_break_case(item, 0, "postgres", Width())
    assert isinstance(case, exp.Case)


def test_declines_root_case():
    # a CASE that IS the item value is handled by case.py, not here.
    item = _second_item("select a, case when x > 0 then 1 end as y from t")
    assert E.nested_break_case(item, 0, "postgres", Width()) is None


def test_declines_no_case():
    item = _second_item("select a, coalesce(sum(amount), 0) as s from t")
    assert E.nested_break_case(item, 0, "postgres", Width()) is None


def test_declines_when_fits_width():
    # nested CASE present but the flat one-line render fits -> no break.
    item = _second_item("select a, coalesce(sum(case when x > 0 then 1 end), 0) as s from t")
    assert E.nested_break_case(item, 0, "postgres", Width()) is None


def test_a_case_with_else_breaks_too():
    """It used to decline. `long_form_case_lines` has always laid out an ELSE —
    this module simply passed `None` where the CASE's own default belonged."""
    body = ("select a, coalesce(sum(case when ledger_event_type > 0.00 "
            "and ledger_event_type < 10.00 then ledger_event_type "
            "else other_long_column_name end), 0.00) as funding_amt from t")
    item = _second_item(body)
    assert E.nested_break_case(item, 0, "postgres", Width()) is not None
    out = format_sql(body + ";", "postgres")
    assert out.warnings == []
    assert "ELSE other_long_column_name" in out.text
    assert ast_equal(body + ";", out.text, "postgres")


# ---- prefix / suffix derivation (from render, not hard-coded) --------------

def test_prefix_and_suffix_derived_from_nodes():
    item = _second_item(_SAMPLE21)
    prefix, _case, suffix = E._match(item.this, "postgres")
    assert prefix == "COALESCE(SUM("
    assert suffix == "), 0.00)"


# ---- geometry: exact column placement --------------------------------------

def test_case_and_end_columns():
    item = _second_item(_SAMPLE21)
    body = E.nested_break_body(item, 0, "postgres", Width())
    # line 0 head seg text carries the wrapper prefix + CASE; CASE therefore
    # starts at item_col(7) + len("COALESCE(SUM(") = 20.
    assert body[0].segs[0].text == "COALESCE(SUM(CASE"
    # END sits at CASE-col + 1 = 21 and tucks the closers.
    end_line = body[-1]
    assert end_line.indent == 21
    assert end_line.segs[-1].text == "END), 0.00)"


# ---- end-to-end: sample 21 byte-exact + idempotent + no warnings -----------

def test_sample21_byte_exact_and_pristine():
    inp, expected = load_pair("21")
    result = format_sql(inp, "postgres")
    assert result.text == expected
    assert result.warnings == []


def test_sample21_idempotent():
    _, expected = load_pair("21")
    assert format_sql(expected, "postgres").text == expected


def test_any_wrapper_breaks_now():
    """A bare `MAX(CASE …)` used to render flat, and before that declined
    outright, because the END offset was golden-proven for `COALESCE(SUM(…))`
    alone and inventing geometry is invisible to `ast_equal`.

    Re-reading sample 21's bytes showed the offset is not an invention to make:
    `END` is three characters and `CASE` is four, so `END` at CASE+1 puts the
    wrapper's closers — glued straight onto it — in the column immediately after
    the CASE keyword, which is where the CASE's own content starts on line 0.
    That relationship holds for any prefix, so there is nothing left to guess.
    """
    stmt = ("select total, max(case when ledger_event_type > 0.00 "
            "and ledger_event_type < 10.00 then ledger_event_type end) "
            "as funding_amt from orders;")
    result = format_sql(stmt, "postgres")
    assert result.warnings == []
    assert result.text == (
        "SELECT total\n"
        "     , MAX(CASE WHEN ledger_event_type > 0.00\n"
        "                 AND ledger_event_type < 10.00\n"
        "                  THEN ledger_event_type\n"
        "            END) AS funding_amt\n"
        "FROM orders;"
    )
    assert ast_equal(stmt, result.text, "postgres")


def test_the_closers_start_where_case_ends():
    """The rule itself, checked on the bytes rather than trusted — for a prefix
    that is not sample 21's, so it cannot pass by coincidence."""
    stmt = ("select total, max(case when ledger_event_type > 0.00 "
            "and ledger_event_type < 10.00 then ledger_event_type end) "
            "as funding_amt from orders;")
    lines = [ln for ln in format_sql(stmt, "postgres").text.split("\n") if ln.strip()]
    case_col = next(ln.index("CASE") for ln in lines if "CASE" in ln)
    end_line = next(ln for ln in lines if ln.strip().startswith("END"))
    assert end_line.index("END") == case_col + 1
    assert end_line.index(")") == case_col + len("CASE")


def test_the_verified_wrapper_still_breaks():
    """The narrow golden-proven shape must not have been widened by accident."""
    _, expected = load_pair("21")
    assert format_sql(expected, "postgres").text == expected
    assert "\n" in expected[expected.index("COALESCE"):expected.index("FROM orders")]
