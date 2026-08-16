from pathlib import Path

from sqlalign.splitter import split_statements

# Anchored to this file, not the cwd, so the suite runs from any directory.
QUERIES_SQL = Path(__file__).resolve().parent.parent / "samples" / "queries.sql"


def test_lossless_roundtrip():
    text = "-- header\nSELECT 1;\n\nSELECT ';' AS s; -- trailer\n"
    assert "".join(split_statements(text)) == text


def test_semicolons_in_strings_and_comments_ignored():
    text = "SELECT ';'; /* ; */ SELECT 2; -- ;\n"
    assert len(split_statements(text)) == 2


def test_dollar_quoted_body_is_one_statement():
    text = ("CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS "
            "$$ BEGIN RETURN 1; END; $$;\nSELECT 1;\n")
    parts = split_statements(text)
    assert len(parts) == 2
    assert "$$" in parts[0] and "SELECT 1" in parts[1]


def test_tagged_dollar_quote():
    text = "CREATE FUNCTION f() RETURNS int AS $fn$ SELECT 1; $fn$ LANGUAGE sql;\n"
    assert len(split_statements(text)) == 1


def test_nested_block_comments():
    text = "SELECT /* a /* b */ c; */ 1;\n"
    assert len(split_statements(text)) == 1


def test_split_never_loses_bytes_on_samples():
    src = QUERIES_SQL.read_text()
    assert "".join(split_statements(src)) == src


def test_quoted_identifiers_with_semicolons_and_escapes():
    text = 'SELECT "a;b", "c""d" FROM t;\nSELECT 2;\n'
    parts = split_statements(text)
    assert len(parts) == 2
    assert "".join(parts) == text


def test_trailing_newline_folds_into_last_statement():
    assert split_statements("SELECT 1;\n") == ["SELECT 1;\n"]


def test_tagged_dollar_quote_with_digit():
    text = ("CREATE FUNCTION f() RETURNS int AS $fn1$ BEGIN RETURN 1; END $fn1$ "
            "LANGUAGE plpgsql;\nSELECT 3;\n")
    assert len(split_statements(text)) == 2


def test_escape_string_backslash_quote():
    text = "SELECT E'it\\'s; fine' FROM t;\nSELECT 2;\n"
    parts = split_statements(text)
    assert len(parts) == 2
    assert "".join(parts) == text


def test_identifier_ending_in_e_not_escape_prefix():
    text = "SELECT CASE'x' WHEN'x' THEN 1 END;\n"  # pathological; just must stay lossless
    assert "".join(split_statements(text)) == text
