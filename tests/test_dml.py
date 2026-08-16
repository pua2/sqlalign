"""DML layout: spacing/adjacency rules and DELETE (no golden fixture)."""
from sqlalign.formatter import format_sql


def test_blank_line_between_two_multiline_statements():
    src = "insert into t (a, b) select a, b from s;\nupdate t set a = 1 where b = 2;\n"
    out = format_sql(src).text
    # both statements format multi-line -> exactly one blank line between them
    assert "\n\nUPDATE t" in out
    assert format_sql(out).text == out                # idempotent


def test_no_blank_line_when_a_statement_is_single_line():
    # a single-line statement beside a multi-line one stays adjacent (no blank)
    src = "truncate table staging;\nupdate t set a = 1 where b = 2;\n"
    out = format_sql(src).text
    assert "\n\n" not in out
    assert format_sql(out).text == out


def test_delete_formats():
    out = format_sql("delete from products where discontinued = true;\n").text
    assert out == "DELETE FROM products\nWHERE discontinued = TRUE;\n"
    assert format_sql(out).text == out                # idempotent


def test_delete_with_alias_and_multi_condition():
    out = format_sql(
        "delete from products p where p.discontinued = true and p.stock = 0;\n").text
    assert out.startswith("DELETE FROM products p\n")
    assert "\n  AND " in out
