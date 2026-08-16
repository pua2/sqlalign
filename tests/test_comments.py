"""Unit tests for the faithful comment engine (comments.py).

sqlglot loses the `--` vs `/* */` style and attaches comments by raw proximity;
the engine recovers style+text from raw source and reclassifies each comment to
the select-list item it authorially leads/trails, declining (Unsupported) any
position it does not model. The golden byte-comparison is the ONLY guard for
comments (ast_equal excludes them), so these tests pin the recovery + routing.
"""
import pytest
import sqlglot
from conftest import load_pair

from sqlalign.casing import parse_dialect
from sqlalign.formatter import format_sql
from sqlalign.layout import Unsupported
from sqlalign.layout import comments as C


def _parse(body):
    return next(e for e in sqlglot.parse(body, read=parse_dialect("postgres")) if e is not None)


# ---- style / text / verbatim recovery -------------------------------------

def test_scan_recovers_line_comment_style_and_text():
    got = C.scan_comments("select a from t -- trailing note\n")
    assert len(got) == 1
    c = got[0]
    assert c.style == "line"
    assert c.text == " trailing note"
    assert c.verbatim == "-- trailing note"


def test_scan_recovers_block_comment_style_and_text():
    got = C.scan_comments("select /* lead me */ a from t")
    assert len(got) == 1
    c = got[0]
    assert c.style == "block"
    assert c.text == " lead me "
    assert c.verbatim == "/* lead me */"


def test_scan_recovers_both_in_source_order():
    src = "select /* b */ a as x, c as y -- t\n"
    got = C.scan_comments(src)
    assert [(c.style, c.text) for c in got] == [("block", " b "), ("line", " t")]


def test_scan_ignores_comment_markers_inside_strings():
    got = C.scan_comments("select '-- not a comment', '/* also not */' from t")
    assert got == []


def test_scan_text_matches_sqlglot_stored_text():
    # The engine's count/text validation depends on raw inner text matching
    # sqlglot's delimiter-stripped node.comments exactly.
    body = ("select cast(signup_ts as date) as signup_date, "
            "/* legacy field, keep until Q4 */ round(x, 2) as ltv "
            "-- rounded for reporting\nfrom t")
    node = _parse(body)
    attached = [c for n in node.walk() if n.comments for c in n.comments]
    scanned = [c.text for c in C.scan_comments(body)]
    assert sorted(scanned) == sorted(attached)


# ---- reattachment: leading vs trailing ------------------------------------

# Sample 12's body as the engine sees it (its prefix `-- #12` line is split off
# upstream, so it is not part of the statement text handed to the engine).
BODY_12 = ("select user_id, coalesce(nullif(trim(display_name), ''), email) as name, "
           "cast(signup_ts as date) as signup_date, "
           "/* legacy field, keep until Q4 */ round(lifetime_value::numeric, 2) as ltv "
           "-- rounded for reporting\nfrom user_profiles where deleted_at is null;")


def test_leading_reattachment_moves_block_to_next_item():
    # sqlglot binds `/* legacy field */` to signup_date by proximity, but it
    # authorially LEADS ltv (it sits after signup_date's comma, before round()).
    node = _parse(BODY_12)
    C.process(node, BODY_12, "postgres")
    ltv = node.expressions[3]
    signup = node.expressions[2]
    assert ltv.meta.get("sqlalign_lead") == "/* legacy field, keep until Q4 */"
    assert signup.meta.get("sqlalign_lead") is None
    assert signup.meta.get("sqlalign_trail") is None


def test_trailing_comment_attaches_to_its_item():
    node = _parse(BODY_12)
    C.process(node, BODY_12, "postgres")
    ltv = node.expressions[3]
    assert ltv.meta.get("sqlalign_trail") == "-- rounded for reporting"


def test_process_strips_comments_from_tree():
    node = _parse(BODY_12)
    C.process(node, BODY_12, "postgres")
    assert all(not n.comments for n in node.walk())


# ---- unmodeled positions decline (passthrough) ----------------------------

def test_comment_in_where_declines():
    body = "select a from t where b /* buried */ = 1"
    node = _parse(body)
    with pytest.raises(Unsupported):
        C.process(node, body, "postgres")


def test_comment_buried_in_expression_declines():
    body = "select coalesce(a /* buried */, b) as x from t"
    node = _parse(body)
    with pytest.raises(Unsupported):
        C.process(node, body, "postgres")


def test_no_comments_is_noop():
    body = "select a, b from t"
    node = _parse(body)
    C.process(node, body, "postgres")  # must not raise
    assert all(not n.comments for n in node.walk())


# ---- end-to-end: sample 12 byte-exact + idempotent + no warnings ----------

def test_sample12_byte_exact_and_pristine():
    inp, expected = load_pair("12")
    result = format_sql(inp, "postgres")
    assert result.text == expected
    assert result.warnings == []


def test_sample12_idempotent_and_original_styles_preserved():
    _, expected = load_pair("12")
    result = format_sql(expected, "postgres")
    assert result.text == expected
    # original styles survive: block leads ltv, line trails ltv — never restyled.
    assert "/* legacy field, keep until Q4 */" in result.text
    assert "-- rounded for reporting" in result.text
    assert "/* rounded for reporting */" not in result.text


def test_a_multiline_block_comment_formats():
    """It used to decline, on the grounds that a Seg carrying a newline corrupts
    align.py's column math.

    That is true, and it is only true where the comment sits INSIDE a row's
    content. Both placements this engine produces put it at the END of a row,
    after every segment that participates in an alignment column — so nothing
    measurable follows the newline, and the shared AS column is unaffected.
    That is what the second assertion checks.
    """
    stmt = "select aaaaa, /* line1\nline2 */ bbbbb as bb from t;"
    result = format_sql(stmt, "postgres")
    assert result.warnings == []
    assert result.text == (
        "SELECT aaaaa /* line1\n"
        "line2 */\n"
        "     , bbbbb AS bb\n"
        "FROM t;"
    )


def test_a_multiline_comment_does_not_skew_the_as_column():
    """The premise of the old decline, tested directly: put the comment on the
    row that SETS the column and every other row must still line up with it."""
    out = format_sql("select aaaaaaaaaa as x, /* multi\n   line */ b as yy, "
                     "c as z from t;", "postgres")
    assert out.warnings == []
    rows = [ln for ln in out.text.split("\n") if " AS " in ln]
    assert len(rows) == 3
    assert len({ln.index(" AS ") for ln in rows}) == 1, out.text
