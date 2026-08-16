"""The `-- sqlalign: skip` directive: a statement whose leading comment block
carries the marker is passed through byte-identical, with no parse, no layout,
and no warning."""
from sqlalign.formatter import format_sql

# A body sqlglot cannot parse (so the non-skip path would emit a parse-error
# warning) — proving the skip check runs *before* any parse attempt.
MALFORMED = "select from where )( group;\n"

# A valid body that formatting would otherwise rewrite (stacked select list).
VALID_MESSY = "select a, b from t;\n"


def test_skip_passes_malformed_through_without_warning():
    src = "-- sqlalign: skip\n" + MALFORMED
    result = format_sql(src)
    assert result.text == src
    assert result.warnings == []


def test_skip_leaves_valid_statement_unformatted():
    src = "-- sqlalign: skip\n" + VALID_MESSY
    result = format_sql(src)
    assert result.text == src            # NOT reformatted despite being valid
    assert result.warnings == []


def test_unmarked_sibling_still_formats():
    src = "-- sqlalign: skip\n" + VALID_MESSY + VALID_MESSY
    result = format_sql(src)
    skipped = "-- sqlalign: skip\n" + VALID_MESSY
    formatted = "SELECT a\n     , b\nFROM t;\n"
    assert result.text == skipped + formatted
    assert result.warnings == []


def test_skip_marker_is_case_insensitive():
    src = "-- SQLAlign:  SKIP\n" + VALID_MESSY
    assert format_sql(src).text == src
