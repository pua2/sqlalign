"""`dialect` as a config key.

sqlalign refuses to GUESS a dialect: sniffing a file cannot be covered by the
safety guarantee, because the handlers emit the keywords of the dialect they
were told about and the AST check cannot see output that is valid SQL but
invalid for the target engine.

Declaring one is a different act. A `dialect` in a committed config is the
author naming the engine once, in a file their team reviews -- more explicit
than a flag typed into an editor's tool settings and never looked at again, and
the only way one editor action can serve a Postgres project and a Redshift
project without the user picking correctly every time.

Discriminating SQL throughout: a quoted identifier renders `[like this]` under
T-SQL and `"like this"` elsewhere, so these tests can tell which grammar ran.
Comparing output that happens to be identical across dialects would assert
nothing.
"""
import pytest

from sqlalign.cli import main
from sqlalign.configfile import ConfigError, load_dialect

QUOTED = 'select "my col" from t;\n'


def _project(tmp_path, config=None, sql=QUOTED):
    (tmp_path / "q.sql").write_text(sql)
    if config is not None:
        (tmp_path / ".sqlalign.toml").write_text(config)
    return tmp_path / "q.sql"


def test_the_config_dialect_is_used(tmp_path, capsys):
    path = _project(tmp_path, 'dialect = "tsql"\n')
    assert main(["--stdout", str(path)]) == 0
    assert "[my col]" in capsys.readouterr().out


def test_without_a_config_the_default_is_postgres(tmp_path, capsys):
    path = _project(tmp_path)
    assert main(["--stdout", str(path)]) == 0
    assert '"my col"' in capsys.readouterr().out


def test_the_flag_beats_the_config(tmp_path, capsys):
    """A one-off run has to be able to override a committed config -- the same
    precedence every style setting already follows."""
    path = _project(tmp_path, 'dialect = "tsql"\n')
    assert main(["--stdout", "--dialect", "postgres", str(path)]) == 0
    assert '"my col"' in capsys.readouterr().out


def test_isolated_ignores_the_config(tmp_path, capsys):
    path = _project(tmp_path, 'dialect = "tsql"\n')
    assert main(["--stdout", "--isolated", str(path)]) == 0
    assert '"my col"' in capsys.readouterr().out


def test_two_projects_in_one_run_each_get_their_own(tmp_path, capsys):
    """The case this exists for: one editor action, one command, projects that
    target different engines."""
    pg = tmp_path / "pg"
    pg.mkdir()
    ts = tmp_path / "ts"
    ts.mkdir()
    a = _project(pg, 'dialect = "postgres"\n')
    b = _project(ts, 'dialect = "tsql"\n')

    assert main(["--stdout", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert '"my col"' in out and "[my col]" in out


def test_an_unknown_dialect_is_refused(tmp_path, capsys):
    """Refused, not attempted -- the same rule the flag follows."""
    path = _project(tmp_path, 'dialect = "mysql"\n')
    assert main(["--stdout", str(path)]) == 2
    err = capsys.readouterr().err
    assert "unknown dialect" in err and "mysql" in err


def test_a_non_string_dialect_is_refused(tmp_path, capsys):
    path = _project(tmp_path, "dialect = 42\n")
    assert main(["--stdout", str(path)]) == 2
    assert "unknown dialect" in capsys.readouterr().err


def test_load_dialect_returns_none_when_unset(tmp_path):
    config = tmp_path / ".sqlalign.toml"
    config.write_text("width = 90\n")
    assert load_dialect(config) is None


def test_load_dialect_raises_on_a_bad_value(tmp_path):
    config = tmp_path / ".sqlalign.toml"
    config.write_text('dialect = "oracle"\n')
    with pytest.raises(ConfigError, match="unknown dialect"):
        load_dialect(config)


def test_dialect_is_not_a_style_field():
    """It selects the grammar to parse and emit, not the layout. In Style it
    would be indistinguishable from a knob, and `preset_style` would carry it."""
    import dataclasses

    from sqlalign.style import Style
    assert "dialect" not in {f.name for f in dataclasses.fields(Style)}


def test_show_config_reports_the_effective_dialect(tmp_path, capsys):
    """--show-config is documented as TOML a reader could paste back. A key it
    omits is a setting the pasted config would silently lose."""
    path = _project(tmp_path, 'dialect = "tsql"\n')
    assert main(["--show-config", str(path)]) == 0
    settings = [line for line in capsys.readouterr().out.splitlines()
                if not line.startswith("#")]
    assert 'dialect = "tsql"' in settings


def test_show_config_output_still_loads_as_a_config(tmp_path, capsys):
    """The round trip that makes the claim true rather than decorative."""
    import tomllib

    path = _project(tmp_path, 'dialect = "tsql"\nwidth = 90\n')
    assert main(["--show-config", str(path)]) == 0
    printed = capsys.readouterr().out
    data = tomllib.loads(printed)
    assert data["dialect"] == "tsql" and data["width"] == 90
