"""Jinja / dbt template protection.

A dbt model is not valid SQL, so before this sqlalign declined the whole file.
Template expressions are masked with SAME-WIDTH placeholders, the normal engine
formats the result, and the originals are restored — so alignment columns are
computed against the real text width and survive restoration.
"""
import pytest
import sqlglot
from conftest import SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style
from sqlalign.templating import has_templating, mask, unmask

DBT = ("select o.id, o.total, c.email from {{ ref('orders') }} o "
       "join {{ ref('customers') }} c on c.id = o.customer_id "
       "where o.status = 'complete';")

OFF = Style(protect_templating=False)


# ---- the mask itself -------------------------------------------------------

def test_mask_preserves_width_exactly():
    """The whole design: a placeholder that is not the same width would shift
    every column computed from it the moment the original is restored."""
    masked, repl = mask(DBT)
    assert len(masked) == len(DBT)
    for placeholder, original in repl.items():
        assert len(placeholder) == len(original)


def test_mask_round_trips():
    masked, repl = mask(DBT)
    assert unmask(masked, repl) == DBT


def test_masked_text_is_parseable_sql():
    masked, _ = mask(DBT)
    assert sqlglot.parse_one(masked, read="postgres") is not None


def test_placeholders_are_distinct():
    """Two expressions must get two placeholders: `repl` is keyed BY placeholder,
    so one shared placeholder would silently collapse them into a single entry
    and restore the wrong original."""
    _masked, repl = mask("select {{ a_long_one }}, {{ b_long_one }} from t;")
    assert sorted(repl.values()) == ["{{ a_long_one }}", "{{ b_long_one }}"]


@pytest.mark.parametrize("snippet", [
    "{{ ref('orders') }}", "{% if enabled %}", "{# a comment here #}",
])
def test_every_default_pattern_is_recognised(snippet):
    assert has_templating(f"select 1 from {snippet};")


def test_too_short_expression_is_refused():
    """Approximating a mask would corrupt the reconstruction, so it declines."""
    with pytest.raises(ValueError):
        mask("select {{x}} from t;")


# ---- formatting a templated model ------------------------------------------

def test_dbt_model_formats():
    out = format_sql(DBT, "postgres").text
    assert "SELECT o.id\n     , o.total\n     , c.email\n" in out
    assert "{{ ref('orders') }}" in out
    assert "{{ ref('customers') }}" in out


def test_alignment_uses_the_real_template_width():
    """The aliases must line up against the width of the Jinja as written, not
    against the placeholder."""
    out = format_sql(DBT, "postgres").text
    from_line = next(ln for ln in out.split("\n") if ln.startswith("FROM"))
    join_line = next(ln for ln in out.split("\n") if ln.startswith("JOIN"))
    assert from_line.index(" o") == join_line.index(" c")
    assert from_line.rstrip().endswith("o")


def test_no_warnings_on_a_templated_model():
    assert format_sql(DBT, "postgres").warnings == []


def test_templated_model_is_idempotent():
    once = format_sql(DBT, "postgres").text
    assert format_sql(once, "postgres").text == once


def test_semantics_preserved_under_the_mask():
    """Raw templated SQL does not parse, so equality is checked on the masked
    forms — which is sound because masking is a bijection over identical
    character positions."""
    masked_in, _repl = mask(DBT)
    masked_out, _ = mask(format_sql(DBT, "postgres").text)
    assert ast_equal(masked_in, masked_out, "postgres")


def test_jinja_statement_blocks_survive():
    sql = "select a from t where {% if enabled %} x = 1 {% endif %};"
    out = format_sql(sql, "postgres").text
    assert "{% if enabled %}" in out and "{% endif %}" in out


def test_template_inside_a_string_literal_is_returned_verbatim():
    sql = "select '{{ not_a_ref }}' as label from t;"
    out = format_sql(sql, "postgres").text
    assert "'{{ not_a_ref }}'" in out


def test_multi_statement_templated_file():
    sql = DBT + "\n\nselect x from {{ ref('other_table') }} t2;"
    out = format_sql(sql, "postgres").text
    assert out.count("{{") == 3
    assert format_sql(out, "postgres").text == out


# ---- the switch ------------------------------------------------------------

def test_protection_can_be_turned_off():
    """Off restores the old behaviour: templated SQL does not parse, so the
    statement passes through byte-identical with a warning."""
    result = format_sql(DBT, "postgres", OFF)
    assert result.text == DBT
    assert result.warnings


def test_too_short_expression_passes_the_file_through():
    sql = "select {{x}} from t;"
    result = format_sql(sql, "postgres")
    assert result.text == sql
    assert any("maskable" in w for w in result.warnings)


def test_untemplated_sql_is_untouched_by_the_feature():
    """Protection must be a no-op when there is nothing to protect."""
    sql = "select a, b from t;"
    assert format_sql(sql, "postgres").text == format_sql(sql, "postgres", OFF).text


def test_default_is_on():
    assert Style().protect_templating is True


# ---- the goldens are unaffected -------------------------------------------

def test_goldens_contain_no_templating():
    """Guard: if a fixture ever gains a `{{`, the masking path would silently
    start running for it and this test should be revisited deliberately."""
    for sid in SAMPLES:
        assert not has_templating(load_pair(sid)[1]), sid
