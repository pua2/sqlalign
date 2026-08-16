"""Directory expansion and exclude globs.

`sqlalign .` was an error — a directory raised `Is a directory` — which blocked the
standard CI invocation. Directories now expand to their `*.sql` files recursively,
in sorted order so a run is reproducible, with `--exclude` / the `exclude` config
key to skip paths.
"""
import pytest

from sqlalign.cli import _expand, main
from sqlalign.configfile import ConfigError, build_style, load_excludes
from sqlalign.style import Style

MESSY = "select a,b from t;\n"


def _tree(root, *relative):
    for rel in relative:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(MESSY)
    return root


def _paths(roots, excludes=(), isolated=False):
    """`_expand`'s four positional arguments, defaulted to the common case: no
    `--exclude` flag, not isolated, no explicit `--config` path."""
    return _expand([str(r) for r in roots], list(excludes), isolated, None)


def _names(roots, excludes=(), isolated=False):
    return [f.name for f in _paths(roots, excludes, isolated)]


# ---- expansion -------------------------------------------------------------

def test_directory_expands_recursively(tmp_path):
    _tree(tmp_path, "a.sql", "models/b.sql", "models/deep/c.sql")
    assert _names([tmp_path]) == ["a.sql", "b.sql", "c.sql"]


def test_expansion_is_sorted_and_reproducible(tmp_path):
    _tree(tmp_path, "z.sql", "a.sql", "m.sql")
    first = _paths([tmp_path])
    assert first == sorted(first)
    assert first == _paths([tmp_path])


def test_non_sql_files_are_ignored(tmp_path):
    _tree(tmp_path, "a.sql")
    (tmp_path / "notes.md").write_text("hi")
    (tmp_path / "b.txt").write_text("hi")
    assert _names([tmp_path]) == ["a.sql"]


def test_explicit_files_still_work(tmp_path):
    _tree(tmp_path, "a.sql")
    assert _names([tmp_path / "a.sql"]) == ["a.sql"]


def test_empty_directory_yields_nothing(tmp_path):
    assert _names([tmp_path]) == []


def test_mixed_files_and_directories(tmp_path):
    _tree(tmp_path, "dir/a.sql", "loose.sql")
    assert _names([tmp_path / "loose.sql", tmp_path / "dir"]) == ["loose.sql", "a.sql"]


# ---- excludes --------------------------------------------------------------

def test_exclude_by_directory_glob(tmp_path):
    _tree(tmp_path, "models/a.sql", "vendor/b.sql")
    assert _names([tmp_path], ["vendor/*"]) == ["a.sql"]


def test_exclude_by_filename_glob(tmp_path):
    _tree(tmp_path, "a.sql", "b.gen.sql")
    assert _names([tmp_path], ["*.gen.sql"]) == ["a.sql"]


def test_multiple_exclude_patterns(tmp_path):
    _tree(tmp_path, "a.sql", "vendor/b.sql", "c.gen.sql")
    assert _names([tmp_path], ["vendor/*", "*.gen.sql"]) == ["a.sql"]


def test_exclude_from_config_file(tmp_path):
    _tree(tmp_path, "models/a.sql", "vendor/b.sql")
    (tmp_path / ".sqlalign.toml").write_text('exclude = ["vendor/*"]\n')
    assert _names([tmp_path]) == ["a.sql"]


def test_cli_and_config_excludes_combine(tmp_path):
    _tree(tmp_path, "a.sql", "vendor/b.sql", "c.gen.sql")
    (tmp_path / ".sqlalign.toml").write_text('exclude = ["vendor/*"]\n')
    assert _names([tmp_path], ["*.gen.sql"]) == ["a.sql"]


def test_isolated_ignores_config_excludes(tmp_path):
    _tree(tmp_path, "models/a.sql", "vendor/b.sql")
    (tmp_path / ".sqlalign.toml").write_text('exclude = ["vendor/*"]\n')
    assert _names([tmp_path], isolated=True) == ["a.sql", "b.sql"]


def test_an_explicitly_named_file_is_never_excluded(tmp_path):
    """Naming a file is a clearer signal of intent than a pattern in a config."""
    _tree(tmp_path, "vendor/b.sql")
    (tmp_path / ".sqlalign.toml").write_text('exclude = ["vendor/*"]\n')
    assert _names([tmp_path / "vendor" / "b.sql"]) == ["b.sql"]


def test_malformed_exclude_is_an_error(tmp_path):
    (tmp_path / ".sqlalign.toml").write_text("exclude = 5\n")
    with pytest.raises(ConfigError):
        load_excludes(tmp_path / ".sqlalign.toml")


def test_exclude_does_not_leak_into_style():
    """`exclude` selects files, not style — it must never reach Style."""
    assert build_style({"exclude": ["vendor/*"]}) == Style()


# ---- end to end ------------------------------------------------------------

def test_cli_formats_a_directory(tmp_path):
    _tree(tmp_path, "models/a.sql", "models/b.sql")
    assert main([str(tmp_path)]) == 0
    assert (tmp_path / "models" / "a.sql").read_text().startswith("SELECT a")
    assert (tmp_path / "models" / "b.sql").read_text().startswith("SELECT a")


def test_cli_check_on_a_directory_reports_each_file(tmp_path, capsys):
    _tree(tmp_path, "a.sql", "b.sql")
    assert main(["--check", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert out.count("would reformat") == 2


def test_cli_exclude_end_to_end(tmp_path):
    _tree(tmp_path, "models/a.sql", "vendor/b.sql")
    assert main(["--exclude", "vendor/*", str(tmp_path)]) == 0
    assert (tmp_path / "models" / "a.sql").read_text().startswith("SELECT")
    assert (tmp_path / "vendor" / "b.sql").read_text() == MESSY     # untouched


def test_cli_on_an_empty_directory_exits_zero(tmp_path):
    assert main(["--check", str(tmp_path)]) == 0
