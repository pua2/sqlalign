"""Config-file discovery, parsing, and precedence.

Until this landed every knob was CLI-only, so a team could not commit its own
style — which is the whole reason the knobs exist. Precedence is
house defaults < config file < command-line flags.
"""
import dataclasses

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.cli import main
from sqlalign.configfile import (
    KNOWN_KEYS,
    ConfigError,
    build_style,
    describe,
    find_config,
    load_settings,
)
from sqlalign.formatter import format_sql
from sqlalign.style import Style

MESSY = "select a, b from t where xx = 1 and y = 2;\n"


def _sql(tmp_path, name="q.sql", text=MESSY):
    p = tmp_path / name
    p.write_text(text)
    return p


def _config(directory, body, name=".sqlalign.toml"):
    p = directory / name
    p.write_text(body)
    return p


# ---- discovery -------------------------------------------------------------

def test_finds_config_beside_the_file(tmp_path):
    _config(tmp_path, 'comma_position = "trailing"\n')
    assert find_config(_sql(tmp_path)) == tmp_path / ".sqlalign.toml"


def test_finds_config_in_a_parent_directory(tmp_path):
    _config(tmp_path, 'comma_position = "trailing"\n')
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config(_sql(nested)) == tmp_path / ".sqlalign.toml"


def test_nearer_config_wins(tmp_path):
    _config(tmp_path, 'comma_position = "trailing"\n')
    nested = tmp_path / "sub"
    nested.mkdir()
    inner = _config(nested, 'comma_position = "leading"\n')
    assert find_config(_sql(nested)) == inner


def test_finds_pyproject_table(tmp_path):
    _config(tmp_path, '[tool.sqlalign]\ncomma_position = "trailing"\n', "pyproject.toml")
    assert find_config(_sql(tmp_path)) == tmp_path / "pyproject.toml"


def test_pyproject_without_our_table_is_ignored(tmp_path):
    _config(tmp_path, '[tool.black]\nline-length = 88\n', "pyproject.toml")
    assert find_config(_sql(tmp_path)) is None


def test_sqlalign_toml_beats_pyproject_in_the_same_directory(tmp_path):
    _config(tmp_path, '[tool.sqlalign]\ncomma_position = "trailing"\n', "pyproject.toml")
    own = _config(tmp_path, 'comma_position = "leading"\n')
    assert find_config(_sql(tmp_path)) == own


def test_no_config_anywhere(tmp_path):
    assert find_config(_sql(tmp_path)) is None


# ---- parsing and validation ------------------------------------------------

def test_reads_every_knob(tmp_path):
    path = _config(tmp_path, """
width = 80
align = false
align_targets = ["aliases", "operators"]
comma_position = "trailing"
boolean_operator_position = "trailing"
on_placement = "own_line"
format_dollar_bodies = false
neq_style = "<>"
decimal_style = "DECIMAL"
table_alias_style = "as"
""")
    style = build_style(load_settings(path)[0])
    assert style.width.width == 80
    assert style.align is False
    assert style.align_targets == frozenset({"aliases", "operators"})
    assert style.comma_position == "trailing"
    assert style.boolean_operator_position == "trailing"
    assert style.on_placement == "own_line"
    assert style.format_dollar_bodies is False
    assert style.neq_style == "<>"
    assert style.decimal_style == "DECIMAL"
    assert style.table_alias_style == "as"


def test_unknown_key_is_an_error(tmp_path):
    """A typo'd key in a committed config would otherwise mean a team believes it
    has a setting it does not have."""
    path = _config(tmp_path, 'comma_postion = "trailing"\n')     # note the typo
    with pytest.raises(ConfigError) as e:
        load_settings(path)
    assert "comma_postion" in str(e.value) and "valid:" in str(e.value)


def test_unknown_key_warns_when_not_strict(tmp_path):
    path = _config(tmp_path, 'comma_postion = "trailing"\n')
    settings, warns = load_settings(path, strict=False)
    assert settings == {} and len(warns) == 1


def test_invalid_value_is_an_error(tmp_path):
    path = _config(tmp_path, 'comma_position = "sideways"\n')
    with pytest.raises(ConfigError) as e:
        build_style(load_settings(path)[0])
    assert "sideways" in str(e.value)


def test_invalid_toml_is_an_error(tmp_path):
    path = _config(tmp_path, "comma_position = \n")
    with pytest.raises(ConfigError) as e:
        load_settings(path)
    assert "invalid TOML" in str(e.value)


def test_wrong_type_names_the_key(tmp_path):
    path = _config(tmp_path, 'width = "wide"\n')
    with pytest.raises(ConfigError) as e:
        build_style(load_settings(path)[0])
    assert "width" in str(e.value)


# ---- precedence ------------------------------------------------------------

def test_flag_overrides_config(tmp_path):
    _config(tmp_path, 'comma_position = "trailing"\n')
    style = build_style(load_settings(tmp_path / ".sqlalign.toml")[0],
                        {"comma_position": "leading"})
    assert style.comma_position == "leading"


def test_unset_flags_do_not_override_config(tmp_path):
    """A None override means "not passed" and must leave the file's value alone —
    the bug this design exists to prevent."""
    _config(tmp_path, 'comma_position = "trailing"\n')
    style = build_style(load_settings(tmp_path / ".sqlalign.toml")[0],
                        {"comma_position": None, "align": None})
    assert style.comma_position == "trailing"


def test_config_absent_gives_house_defaults():
    assert build_style({}) == Style()


# ---- end to end through the CLI --------------------------------------------

def test_cli_uses_a_discovered_config(tmp_path):
    _config(tmp_path, 'comma_position = "trailing"\n')
    sql = _sql(tmp_path)
    assert main([str(sql)]) == 0
    assert "SELECT a,\n" in sql.read_text()


def test_cli_flag_beats_the_config(tmp_path):
    _config(tmp_path, 'comma_position = "trailing"\n')
    sql = _sql(tmp_path)
    assert main(["--comma-position", "leading", str(sql)]) == 0
    assert "\n     , b" in sql.read_text()


def test_cli_isolated_ignores_the_config(tmp_path):
    _config(tmp_path, 'comma_position = "trailing"\n')
    sql = _sql(tmp_path)
    assert main(["--isolated", str(sql)]) == 0
    assert "\n     , b" in sql.read_text()


def test_cli_explicit_config_path(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    cfg = _config(other, 'comma_position = "trailing"\n', "custom.toml")
    sql = _sql(tmp_path)
    assert main(["--config", str(cfg), str(sql)]) == 0
    assert "SELECT a,\n" in sql.read_text()


def test_cli_broken_config_exits_two_and_leaves_the_file(tmp_path):
    _config(tmp_path, 'comma_postion = "trailing"\n')
    sql = _sql(tmp_path)
    assert main([str(sql)]) == 2
    assert sql.read_text() == MESSY           # untouched


def test_cli_show_config(tmp_path, capsys):
    _config(tmp_path, 'comma_position = "trailing"\nwidth = 80\n')
    assert main(["--show-config", str(_sql(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert 'comma_position = "trailing"' in out
    assert "width = 80" in out
    assert ".sqlalign.toml" in out            # says where it came from


def test_cli_show_config_without_a_config(tmp_path, capsys):
    assert main(["--show-config", str(_sql(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "built-in defaults" in out
    assert 'comma_position = "leading"' in out


def test_show_config_output_is_valid_config(tmp_path):
    """Round trip: what --show-config prints must be loadable as a config."""
    assert main(["--show-config", str(_sql(tmp_path))]) == 0
    path = _config(tmp_path, describe(Style()))
    assert build_style(load_settings(path)[0]) == Style()


def test_per_file_config_resolution(tmp_path):
    """One invocation spanning two directories picks up each one's own config."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    _config(a, 'comma_position = "trailing"\n')
    _config(b, 'comma_position = "leading"\n')
    sql_a, sql_b = _sql(a), _sql(b)
    assert main([str(sql_a), str(sql_b)]) == 0
    assert "SELECT a,\n" in sql_a.read_text()
    assert "\n     , b" in sql_b.read_text()


def test_config_does_not_change_the_default_goldens(tmp_path):
    """An isolated run must still be the house style."""
    for sid in SAMPLES:
        inp, expected = load_pair(sid)
        assert format_sql(inp, DIALECTS.get(sid, "postgres"), build_style({})).text == expected


def test_every_style_field_is_reachable_from_a_config_file():
    """A guard against the failure this test was written after: `protect_templating`
    shipped without being added to KNOWN_KEYS, so it could be set in code but not
    in the config file anyone would actually use. Any new Style field must be
    settable, or deliberately excluded here with a reason."""
    fields = {f.name for f in dataclasses.fields(Style)}
    missing = fields - KNOWN_KEYS
    assert not missing, f"Style fields not settable from a config file: {sorted(missing)}"


def test_show_config_lists_every_style_field():
    """--show-config must be a complete picture, or it misleads about what is set."""
    text = describe(Style())
    for f in dataclasses.fields(Style):
        assert f.name in text, f.name
