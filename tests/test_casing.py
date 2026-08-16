import sqlglot

from sqlalign.casing import render_expr


def q(sql, read="postgres"):
    return sqlglot.parse_one(sql, read=read)


def test_functions_and_keywords_upper():
    assert render_expr(q("select coalesce(nullif(trim(a), ''), b)").expressions[0],
                       "postgres") == "COALESCE(NULLIF(TRIM(a), ''), b)"


def test_boolean_null_upper_identifiers_untouched():
    e = q("select x = true, y is null").expressions
    assert render_expr(e[0], "postgres") == "x = TRUE"
    assert render_expr(e[1], "postgres") == "y IS NULL"


def test_cast_forms_preserved():
    assert render_expr(q("select a::numeric").expressions[0], "postgres") == "a::NUMERIC"
    assert render_expr(q("select cast(a as date)").expressions[0], "postgres") == "CAST(a AS DATE)"


def test_comma_space_in_args():
    assert render_expr(q("select round(v,2)").expressions[0], "postgres") == "ROUND(v, 2)"


def test_nested_cast_form_preserved():
    assert render_expr(q("select round(lifetime_value::numeric, 2)").expressions[0],
                       "postgres") == "ROUND(lifetime_value::NUMERIC, 2)"


def test_parameterized_numeric_preserved():
    assert render_expr(q("select a::numeric(12,2)").expressions[0],
                       "postgres") == "a::NUMERIC(12, 2)"


def test_decimal_canonicalizes_to_numeric():
    assert render_expr(q("select cast(a as decimal(10,2))").expressions[0],
                       "postgres") == "CAST(a AS NUMERIC(10, 2))"


def test_chained_casts_preserved():
    assert render_expr(q("select a::int::text").expressions[0], "postgres") == "a::INT::TEXT"


def test_redshift_uses_house_rules():
    node = q("select a::decimal(10,2)", "redshift").expressions[0]
    assert render_expr(node, "redshift") == "a::NUMERIC(10, 2)"
