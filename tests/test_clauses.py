"""Clause-rendering rulings (panel task 7 fixes 4, 5)."""
from sqlalign.formatter import ast_equal, format_sql

# --- FIX 4: NEQ canonicalized to '!=' (house form) ---------------------------
# sqlglot erases the '!=' vs '<>' source spelling at parse (both -> exp.NEQ), so
# "kept as written" is impossible through the AST. The house form is '!='.

def test_neq_source_bang_equal_renders_bang_equal():
    src = "select a from t where a != b;\n"
    result = format_sql(src)
    assert "a != b" in result.text
    assert "<>" not in result.text
    assert ast_equal(src, result.text, "postgres")


def test_neq_source_diamond_canonicalizes_to_bang_equal():
    src = "select a from t where a <> b;\n"
    result = format_sql(src)
    assert "a != b" in result.text           # '<>' canonicalized to '!='
    assert "<>" not in result.text
    assert ast_equal(src, result.text, "postgres")


# --- FIX 5: LIMIT + OFFSET share one line -------------------------

def _clause_lines(text, *keywords):
    return [ln for ln in text.splitlines() if any(k in ln for k in keywords)]


def test_limit_and_offset_share_one_line():
    src = "select a from t limit 10 offset 5;\n"
    result = format_sql(src)
    assert "LIMIT 10 OFFSET 5" in result.text
    assert len(_clause_lines(result.text, "LIMIT", "OFFSET")) == 1
    assert ast_equal(src, result.text, "postgres")


def test_limit_only_stays_single_line():
    result = format_sql("select a from t limit 10;\n")
    assert "LIMIT 10" in result.text
    assert len(_clause_lines(result.text, "LIMIT")) == 1


def test_offset_only_stays_single_line():
    result = format_sql("select a from t offset 5;\n")
    assert "OFFSET 5" in result.text
    assert len(_clause_lines(result.text, "OFFSET")) == 1
