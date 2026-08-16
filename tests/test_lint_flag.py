"""`--lint`: format, then run sqlfluff over the result, in one command.

This does not merge the two jobs — it saves running two tools over the same
files, and guarantees they are not fighting: the linter never reports the layout
sqlalign has just produced, because it runs under the coexistence config unless
the team has committed one of their own.

That precedence is the design decision worth testing. A committed `.sqlfluff` is
a decision someone made, so it wins; the generated config is only the fallback
for when there is nothing to respect.
"""
import subprocess
import sys

import pytest

from sqlalign.cli import main
from sqlalign.lint import discovered_config

pytest.importorskip("sqlfluff")

MESSY = "select cust.id, ord.total from customers cust join orders ord on cust.id = ord.cid;\n"
# Formatted, and free of the semantic findings sqlfluff makes about the above.
# `INNER JOIN` rather than `JOIN` matters: AM05 wants joins fully qualified.
CLEAN = ("SELECT cust.id\n"
         "     , ord.total\n"
         "FROM customers    cust\n"
         "INNER JOIN orders ord ON cust.id = ord.cid\n"
         "WHERE ord.total > 0;\n")


def _sql(tmp_path, text=MESSY, name="q.sql"):
    path = tmp_path / name
    path.write_text(text)
    return path


# ---- it formats AND lints ------------------------------------------------

def test_it_formats_and_then_lints(tmp_path, capsys):
    path = _sql(tmp_path)
    main(["--lint", str(path)])
    out = capsys.readouterr()
    assert path.read_text().startswith("SELECT cust.id\n     , ord.total"), "did not format"
    assert "All Finished!" in out.out, "did not lint"


def test_findings_exit_1(tmp_path):
    """AM05/ST09 fire on this SQL — real observations about the source."""
    assert main(["--lint", "--check", str(_sql(tmp_path))]) == 1


def test_no_findings_exit_0(tmp_path, capsys):
    path = _sql(tmp_path, CLEAN)
    assert main(["--lint", str(path)]) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_it_lints_what_would_be_written_not_what_is_on_disk(tmp_path, capsys):
    """Under --check nothing is written, so linting the file would report on the
    unformatted source. The formatted text is what gets linted."""
    path = _sql(tmp_path, "select   1   as    x;\n")
    main(["--lint", "--check", str(path)])
    assert "LT01" not in capsys.readouterr().out, "linted the unformatted source"


def test_findings_are_reported_against_the_real_filename(tmp_path, capsys):
    path = _sql(tmp_path)
    main(["--lint", "--check", str(path)])
    out = capsys.readouterr().out
    assert str(path) in out
    assert "sqlalign-" not in out, "leaked the scratch filename"


def test_no_scratch_files_are_left_behind(tmp_path):
    main(["--lint", "--check", str(_sql(tmp_path))])
    assert [p.name for p in tmp_path.iterdir()] == ["q.sql"]


# ---- config precedence ---------------------------------------------------

def test_a_committed_sqlfluff_wins(tmp_path, capsys):
    """Their file, their rules — silently overriding it with a generated one
    would be the wrong kind of helpful."""
    (tmp_path / ".sqlfluff").write_text(
        "[sqlfluff]\ndialect = postgres\n"
        "exclude_rules = layout, structure.join_condition_order, ambiguous.join\n")
    main(["--lint", "--check", str(_sql(tmp_path))])
    out = capsys.readouterr().out
    assert "ST09" not in out and "AM05" not in out, "ignored the committed config"


def test_without_one_the_generated_config_is_used(tmp_path, capsys):
    """No config to respect: the fallback must at least keep layout quiet, which
    is the whole reason the generated config exists."""
    main(["--lint", "--check", str(_sql(tmp_path))])
    assert "[layout." not in capsys.readouterr().out


def test_discovery_walks_up_the_tree(tmp_path):
    (tmp_path / ".sqlfluff").write_text("[sqlfluff]\ndialect = postgres\n")
    nested = tmp_path / "models" / "staging"
    nested.mkdir(parents=True)
    assert discovered_config(nested / "q.sql") == tmp_path / ".sqlfluff"


def test_a_pyproject_without_a_sqlfluff_section_is_not_a_config(tmp_path):
    """Almost every Python project has a pyproject.toml; only one that actually
    carries sqlfluff settings counts as a decision to respect."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert discovered_config(tmp_path / "q.sql") is None

    (tmp_path / "pyproject.toml").write_text('[tool.sqlfluff]\ndialect = "postgres"\n')
    assert discovered_config(tmp_path / "q.sql") == tmp_path / "pyproject.toml"


# ---- it stays optional ---------------------------------------------------

def test_sqlfluff_is_not_a_runtime_dependency():
    """Importing and running sqlalign must not need sqlfluff. Checked in a
    subprocess with the module blocked, since it is importable in this env."""
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name == 'sqlfluff' or name.startswith('sqlfluff.'):\n"
        "            raise ImportError('blocked')\n"
        "sys.meta_path.insert(0, Block())\n"
        "from sqlalign.formatter import format_sql\n"
        "from sqlalign.cli import main\n"
        "print(format_sql('select 1;', 'postgres').text)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "SELECT 1" in result.stdout


def test_missing_sqlfluff_is_an_actionable_message(tmp_path, capsys, monkeypatch):
    import sqlalign.cli as cli
    from sqlalign.lint import LintUnavailable

    def unavailable():
        raise LintUnavailable(
            "--lint needs sqlfluff, which is an optional dependency. "
            "Install it with: pip install 'sqlalign[lint]'")

    monkeypatch.setattr(cli, "version_warning", unavailable)
    assert main(["--lint", "--check", str(_sql(tmp_path))]) == 2
    err = capsys.readouterr().err
    assert "sqlalign[lint]" in err, err


def test_a_version_mismatch_warns(tmp_path, capsys, monkeypatch):
    import sqlalign.lint as lintmod
    monkeypatch.setattr(lintmod, "sqlfluff_version", lambda: "9.9.9")
    import sqlalign.cli as cli
    monkeypatch.setattr(cli, "version_warning", lintmod.version_warning)
    main(["--lint", "--check", str(_sql(tmp_path))])
    assert "rules may have moved" in capsys.readouterr().err
