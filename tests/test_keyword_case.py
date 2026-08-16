"""`Style.keyword_case` — upper (house) or lower.

Driven by the AST rather than a keyword list, because a list-based pass is both
unsafe (`FORMAT`, `ROWS`, `RANGE`, `COMMENT`, `END`, `FILTER` are real column names
AND tokenizer keywords) and incomplete (`BY`, `CAST`, `GROUP`, `ORDER`, `PRIMARY`,
`KEY` are keywords that are NOT in that list). The rule inverts: lower every bare
word EXCEPT the ones the parse tree names as identifiers.
"""
import re

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.cli import main
from sqlalign.formatter import ast_equal, format_sql
from sqlalign.keywordcase import identifier_names
from sqlalign.style import Style

LOWER = Style(keyword_case="lower")


def _code_only(text: str) -> str:
    """Strip comments and string literals — what remains is what sqlalign chose
    to render, so in lower mode it must contain no capital letters at all."""
    text = re.sub(r"--[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"'(?:[^']|'')*'", "''", text)
    # T-SQL: a [bracketed] identifier is the user's text, and GO is a batch
    # separator preserved verbatim -- neither is something sqlalign renders.
    text = re.sub(r"\[[^\]]*\]", "[]", text)
    text = re.sub(r"^[ \t]*GO[ \t]*$", "", text, flags=re.MULTILINE)
    return text


# ---- completeness: the strongest statement of correctness ------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_lower_leaves_no_capital_anywhere(sid):
    """Every golden uses lowercase identifiers, so in lower mode the output must
    be entirely lowercase outside comments and string literals. This is a
    COMPLETE check: a keyword the pass failed to reach shows up as a capital."""
    inp = load_pair(sid)[0]
    out = format_sql(inp, DIALECTS.get(sid, "postgres"), LOWER).text
    leftovers = sorted(set(re.findall(r"[A-Z][A-Z_0-9]*", _code_only(out))))
    assert not leftovers, leftovers


@pytest.mark.parametrize("sid", SAMPLES)
def test_lower_preserves_semantics(sid):
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    assert ast_equal(inp, format_sql(inp, dialect, LOWER).text, dialect)


@pytest.mark.parametrize("sid", SAMPLES)
def test_lower_is_idempotent(sid):
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    once = format_sql(inp, dialect, LOWER).text
    assert format_sql(once, dialect, LOWER).text == once


@pytest.mark.parametrize("sid", SAMPLES)
def test_casing_never_moves_a_column(sid):
    """Case changes are character-for-character, so every line must keep its
    exact length and every column its position."""
    inp = load_pair(sid)[0]
    dialect = DIALECTS.get(sid, "postgres")
    upper = format_sql(inp, dialect, Style()).text
    lower = format_sql(inp, dialect, LOWER).text
    assert ([len(line) for line in lower.split("\n")]
            == [len(line) for line in upper.split("\n")])
    assert lower.lower() == upper.lower()


# ---- the cases that defeat a word list ------------------------------------

@pytest.mark.parametrize("name", ["FORMAT", "ROWS", "RANGE", "COMMENT", "FILTER"])
def test_uppercase_identifier_colliding_with_a_keyword_survives(name):
    """The exact failure a keyword-list pass would produce: lowering the column
    changes it, the safety net rejects the statement, and the file silently goes
    through unformatted."""
    sql = f"SELECT {name} FROM t WHERE {name} IS NOT NULL;"
    result = format_sql(sql, "postgres", LOWER)
    assert f"select {name}\n" in result.text        # keyword lowered, name kept
    assert result.warnings == []                     # nothing declined
    assert ast_equal(sql, result.text, "postgres")


def test_quoted_identifier_is_untouched():
    sql = 'SELECT "MixedCase" FROM t;'
    assert '"MixedCase"' in format_sql(sql, "postgres", LOWER).text


def test_string_literal_contents_are_untouched():
    sql = "SELECT a FROM t WHERE s = 'KEEP This EXACT';"
    assert "'KEEP This EXACT'" in format_sql(sql, "postgres", LOWER).text


def test_comment_text_is_untouched():
    out = format_sql(load_pair("12")[0], "postgres", LOWER).text
    assert "/* legacy field, keep until Q4 */" in out
    assert "-- rounded for reporting" in out


@pytest.mark.parametrize("word", ["by", "cast", "group", "order", "primary", "key"])
def test_keywords_missing_from_the_tokenizer_list_are_still_lowered(word):
    """These are keywords that are NOT in sqlglot's single-word keyword set, so a
    list-based pass would leave them upper while lowering everything around."""
    out = format_sql(load_pair("14")[0], "postgres", LOWER).text  # CREATE TABLE
    out += format_sql(load_pair("05")[0], "postgres", LOWER).text  # GROUP BY/ORDER BY
    out += format_sql(load_pair("12")[0], "postgres", LOWER).text  # CAST
    assert word.upper() not in _code_only(out)


# ---- plpgsql bodies --------------------------------------------------------

def test_plpgsql_body_lowers_and_keeps_its_variables():
    """A dollar-quoted body parses as ONE opaque string, so its identifiers are
    invisible to the outer tree and must be collected separately."""
    out = format_sql(load_pair("19")[0], "postgres", LOWER).text
    assert "declare v_ltv numeric;" in out
    assert "select sum(total) into v_ltv" in out
    assert "end if;" in out


def test_body_identifiers_are_collected():
    names = identifier_names(load_pair("19")[1], "postgres")
    assert {"v_ltv", "orders", "customer_id"} <= names


# ---- defaults and plumbing ------------------------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_upper_reproduces_every_golden(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres"), Style()).text == expected


def test_default_is_upper():
    assert Style().keyword_case == "upper"


@pytest.mark.parametrize("bad", ["Upper", "preserve", "capitalize", ""])
def test_invalid_value_rejected(bad):
    with pytest.raises(ValueError):
        Style(keyword_case=bad)


def test_config_and_cli(tmp_path):
    sql = tmp_path / "q.sql"
    sql.write_text("select a from t;\n")
    assert main(["--keyword-case", "lower", str(sql)]) == 0
    assert sql.read_text().startswith("select a")

    (tmp_path / ".sqlalign.toml").write_text('keyword_case = "upper"\n')
    sql.write_text("select a from t;\n")
    assert main([str(sql)]) == 0
    assert sql.read_text().startswith("SELECT a")


def test_composes_with_other_knobs():
    style = Style(keyword_case="lower", comma_position="trailing", align=False)
    out = format_sql("select a, bb from t where x = 1 and y = 2;", "postgres", style).text
    assert out == "select a,\n       bb\nfrom t\nwhere x = 1\n  and y = 2;"
