"""`--init`, which writes the first `.sqlalign.toml` a project has.

The starter is emitted entirely commented out. That is the property worth
protecting: a file that arrived pinning eighteen settings would freeze a team on
whatever the defaults were the day they ran it, and call that a choice they made.
Uncommenting is the choice.

`--preset` is the exception and is written live, because picking one is the
decision the reader just made -- and the commented values below it then show
what that preset actually does.
"""
import pytest
import tomllib

from sqlalign.cli import main
from sqlalign.configfile import starter
from sqlalign.style import PRESETS, SETTING_SUMMARIES, preset_style

SQL = "select a, b from customers c join orders o on o.cid = c.id;\n"


def _init(tmp_path, monkeypatch, *args):
    monkeypatch.chdir(tmp_path)
    code = main(["--init", *args])
    return code, tmp_path / ".sqlalign.toml"


def test_it_writes_a_config(tmp_path, monkeypatch, capsys):
    code, path = _init(tmp_path, monkeypatch)
    assert code == 0
    assert path.exists()
    assert "wrote .sqlalign.toml" in capsys.readouterr().out


def test_the_result_is_valid_toml(tmp_path, monkeypatch):
    """A starter that does not parse is worse than no starter: the next command
    the reader runs fails, and the tool wrote the file that broke it."""
    _, path = _init(tmp_path, monkeypatch)
    tomllib.loads(path.read_text())


def test_a_plain_starter_changes_nothing(tmp_path, monkeypatch):
    """Everything commented means the file is inert. Verified by formatting the
    same SQL with and without it rather than by reading the text."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "q.sql").write_text(SQL)
    main(["--init"])
    with_file = main(["--check", "q.sql"])
    (tmp_path / ".sqlalign.toml").unlink()
    assert with_file == main(["--check", "q.sql"])


def test_a_preset_is_written_live_and_applies(tmp_path, monkeypatch):
    _, path = _init(tmp_path, monkeypatch, "--preset", "compact")
    assert tomllib.loads(path.read_text())["preset"] == "compact"


def test_an_uncommented_setting_takes_effect(tmp_path, monkeypatch):
    """The file is a menu, so uncommenting has to be all it takes."""
    _, path = _init(tmp_path, monkeypatch)
    path.write_text(path.read_text().replace(
        '# keyword_case = "upper"', 'keyword_case = "lower"'))
    assert tomllib.loads(path.read_text())["keyword_case"] == "lower"


def test_it_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    """A config is a decision a team already made; a starter is only ever the
    first one."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sqlalign.toml").write_text("width = 80\n")
    assert main(["--init"]) == 2
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / ".sqlalign.toml").read_text() == "width = 80\n"


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_produces_a_loadable_starter(preset):
    tomllib.loads(starter(preset_style(preset), preset))


def test_every_setting_appears_with_its_summary():
    """The starter doubles as the reference for someone who never opens the
    docs, so a setting missing from it is a setting they will not find."""
    text = starter(preset_style("house"))
    for name, summary in SETTING_SUMMARIES.items():
        assert f"# {summary}" in text, f"{name} has no comment"
        assert name in text, f"{name} is not in the starter"


def test_the_summaries_cover_every_style_field():
    """`SETTING_SUMMARIES` lives in the package because both the starter and the
    docs read it. A field with no summary is invisible in both."""
    import dataclasses

    from sqlalign.style import Style
    fields = {f.name for f in dataclasses.fields(Style)}
    assert fields == set(SETTING_SUMMARIES), fields ^ set(SETTING_SUMMARIES)


def test_a_broken_config_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    """`--init` resolves config like any other mode, so it has to fail like one:
    a `sqlalign:` line and exit 2. It crashed with the raw ConfigError instead,
    exiting 1 -- which the exit-code table reserves for --check/--diff, so CI
    keying on 1-vs-2 would read a broken config as "would reformat"."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sqlalign.toml").unlink(missing_ok=True)
    parent_config = tmp_path / "up"
    parent_config.mkdir()
    (parent_config / ".sqlalign.toml").write_text('comma_postion = "trailing"\n')
    child = parent_config / "sub"
    child.mkdir()
    monkeypatch.chdir(child)

    assert main(["--init"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("sqlalign:"), err
    assert not (child / ".sqlalign.toml").exists()


def test_an_unwritable_directory_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    tmp_path.chmod(0o555)
    try:
        assert main(["--init"]) == 2
        assert "sqlalign:" in capsys.readouterr().err
    finally:
        tmp_path.chmod(0o755)
