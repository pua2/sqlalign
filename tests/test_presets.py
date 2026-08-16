"""Named presets — one word instead of nine knobs.

A preset sets a BASE only: config-file keys and command-line flags layer on top,
so `preset = "compact"` plus `comma_position = "trailing"` means both rather than
either/or. Presets are the market-facing surface, so each one must correspond to
a real constituency and must be honest about what it does.
"""
import inspect

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

import sqlalign.style as style_module
from sqlalign.cli import main
from sqlalign.configfile import ConfigError, build_style
from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import PRESETS, Style, preset_style

SQL = "select a, bb from t where xx = 1 and y = 2;"


def test_house_preset_is_the_default():
    assert preset_style("house") == Style()


@pytest.mark.parametrize("sid", SAMPLES)
def test_house_preset_reproduces_every_golden(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres"), preset_style("house")).text == expected


def test_compact_preset_drops_the_padding():
    out = format_sql(SQL, "postgres", preset_style("compact")).text
    assert out == "SELECT a\n     , bb\nFROM t\nWHERE xx = 1\n  AND y = 2;"


def test_trailing_preset_moves_both_separators():
    out = format_sql(SQL, "postgres", preset_style("trailing")).text
    assert out == "SELECT a,\n       bb\nFROM t\nWHERE xx = 1 AND\n      y  = 2;"


def test_presets_are_distinct():
    """A preset that formats identically to another is not worth its name."""
    outs = {name: format_sql(SQL, "postgres", preset_style(name)).text for name in PRESETS}
    assert len(set(outs.values())) == len(PRESETS), outs


@pytest.mark.parametrize("name", sorted(PRESETS))
@pytest.mark.parametrize("sid", ["13", "08", "06"])
def test_every_preset_preserves_semantics_and_is_idempotent(name, sid):
    inp = load_pair(sid)[0]
    style = preset_style(name)
    out = format_sql(inp, "postgres", style).text
    assert ast_equal(inp, out, "postgres")
    assert format_sql(out, "postgres", style).text == out


def test_unknown_preset_names_the_valid_ones():
    with pytest.raises(ValueError) as e:
        preset_style("oracle")
    assert "oracle" in str(e.value) and "house" in str(e.value)


# ---- layering --------------------------------------------------------------

def test_explicit_key_layers_over_the_preset():
    style = build_style({"preset": "compact", "comma_position": "trailing"})
    assert style.align is False                 # from the preset
    assert style.comma_position == "trailing"   # from the explicit key


def test_flag_layers_over_a_preset_in_the_config(tmp_path):
    (tmp_path / ".sqlalign.toml").write_text('preset = "trailing"\n')
    sql = tmp_path / "q.sql"
    sql.write_text(SQL + "\n")
    assert main(["--comma-position", "leading", str(sql)]) == 0
    text = sql.read_text()
    assert "\n     , bb" in text                # flag won for commas
    assert "xx = 1 AND\n" in text               # preset still supplies booleans


def test_preset_from_config_file(tmp_path):
    (tmp_path / ".sqlalign.toml").write_text('preset = "trailing"\n')
    sql = tmp_path / "q.sql"
    sql.write_text(SQL + "\n")
    assert main([str(sql)]) == 0
    assert "SELECT a,\n" in sql.read_text()


def test_cli_preset_beats_config_preset(tmp_path):
    (tmp_path / ".sqlalign.toml").write_text('preset = "trailing"\n')
    sql = tmp_path / "q.sql"
    sql.write_text(SQL + "\n")
    assert main(["--preset", "house", str(sql)]) == 0
    assert "\n     , bb" in sql.read_text()


def test_unknown_preset_in_config_is_an_error():
    with pytest.raises(ConfigError) as e:
        build_style({"preset": "nope"})
    assert "nope" in str(e.value) and "valid:" in str(e.value)


def test_show_config_expands_the_preset(tmp_path, capsys):
    """A preset must be visible as concrete values, so a team can see exactly
    what it selected rather than trusting a name."""
    (tmp_path / ".sqlalign.toml").write_text('preset = "trailing"\n')
    sql = tmp_path / "q.sql"
    sql.write_text(SQL + "\n")
    assert main(["--show-config", str(sql)]) == 0
    out = capsys.readouterr().out
    assert 'comma_position = "trailing"' in out
    assert 'boolean_operator_position = "trailing"' in out


def test_dbt_preset_matches_dbt_conventions():
    """This preset was withheld until `keyword_case` existed, on the grounds that
    getting the commas right and the casing wrong would look official while being
    wrong. Now that casing, comma position and padding are all expressible it is
    substantially accurate — so pin exactly what it claims."""
    style = preset_style("dbt")
    assert style.keyword_case == "lower"
    assert style.comma_position == "trailing"
    assert style.align is False
    # dbt's own style guide stacks the select list under a bare `select`,
    # indented 4 — the preset claimed dbt conventions while keeping the first
    # item on the `select` line, which is the one shape dbt never writes.
    assert style.select_placement == "own_line"
    assert style.select_indent == 4
    out = format_sql(SQL, "postgres", style).text
    assert out == ("select\n    a,\n    bb\nfrom t\nwhere xx = 1\n  and y = 2;")


def test_dbt_preset_deviation_is_documented():
    """Its one remaining deviation (a CTE body indents 2, not dbt's 4) must stay
    stated in the source rather than discovered by a user."""
    source = inspect.getsource(style_module)
    assert "deviation" in source.lower()
    assert "CTE body 2" in source or "indents a CTE body 2" in source


# ---- gitlab -----------------------------------------------------------------

GITLAB_INPUT = """select my_data.field_1 as detailed_field_1, my_data.field_2 as b,
count(*) as number_of_records
from my_data
left join some_cte on my_data.id = some_cte.id
where my_data.field_1 = 'abc' and my_data.field_2 = 'def'
group by 1, 2
having count(*) > 1
order by 3 desc;"""

# Transcribed from the "Example Code" section of GitLab's SQL Style Guide, which
# states it "has been processed though SQLFluff linter and had the style guide
# applied" -- so it is authoritative over their prose examples, which use a
# 4-space step where their .sqlfluff config and this section use 2.
GITLAB_EXPECTED = """SELECT
  my_data.field_1 AS detailed_field_1,
  my_data.field_2 AS b,
  COUNT(*)        AS number_of_records
FROM my_data
LEFT JOIN some_cte
  ON my_data.id = some_cte.id
WHERE my_data.field_1 = 'abc'
  AND my_data.field_2 = 'def'
GROUP BY 1, 2
HAVING COUNT(*) > 1
ORDER BY 3 DESC;"""


def test_gitlab_preset_reproduces_their_published_example():
    assert format_sql(GITLAB_INPUT, "postgres", preset_style("gitlab")).text == GITLAB_EXPECTED


def test_gitlab_aligns_column_aliases_but_not_table_aliases():
    """Their config asks for exactly one alignment: "aligning column aliases
    within the SELECT statement". Table aliases must stay unpadded, which is why
    the `aliases` target had to split into `column_aliases`/`table_aliases`."""
    out = format_sql(
        "select bf.a as acct, dd.fiscal_year as fy from budget_forecast as bf "
        "left join date_details as dd on dd.first_day = bf.period;",
        "postgres", preset_style("gitlab")).text
    assert "bf.a           AS acct,\n" in out          # column aliases aligned
    assert "FROM budget_forecast AS bf\n" in out       # table aliases not padded
    assert "LEFT JOIN date_details AS dd\n" in out


def test_gitlab_keeps_the_as_their_guide_requires():
    """"Use the AS operator when aliasing a column or table." sqlglot destroys
    the distinction at parse time, so this is `table_alias_style`, not a
    passthrough."""
    style = preset_style("gitlab")
    assert style.table_alias_style == "as"
    assert "FROM orders AS o" in format_sql("select 1 from orders o;", "postgres", style).text
