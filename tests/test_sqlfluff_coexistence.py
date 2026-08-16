"""`--print-sqlfluff-config`: the config that lets sqlfluff run next to sqlalign.

The two tools overlap, and left alone they fight. Run stock `sqlfluff fix` over
sqlalign's output and it rewrites the whole thing — commas move, the columns
collapse, SELECT and WHERE get restructured. The generated config resolves that
the way Prettier and `eslint-config-prettier` did: the formatter owns layout, and
the linter is told to stop having opinions about it.

These tests run the real sqlfluff rather than asserting on the generated text,
because the text is only interesting if the linter agrees with it. Each one runs
in its own tmp directory: sqlfluff auto-discovers a `.sqlfluff` from the working
directory, so a test that shells out from the repo root would silently pick up
the repository's own (very different) gate config and prove nothing.
"""
import subprocess
import sys

import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.cli import main
from sqlalign.formatter import format_sql
from sqlalign.sqlfluffconfig import TESTED_SQLFLUFF, sqlfluff_config
from sqlalign.style import HOUSE, Style, preset_style

pytest.importorskip("sqlfluff")

SQL = ("select cust.customer_id, cust.email, ord.total from customers cust "
       "inner join orders ord on cust.customer_id = ord.customer_id "
       "where ord.total > 0 and cust.segment = 'ent';")


def _run(args, cwd):
    return subprocess.run([sys.executable, "-m", "sqlfluff", *args],
                          capture_output=True, text=True, cwd=cwd)


def _sandbox(tmp_path, sql_text, config=None):
    """A directory holding one .sql file, and a .sqlfluff only if asked for."""
    (tmp_path / "q.sql").write_text(sql_text)
    if config is not None:
        (tmp_path / ".sqlfluff").write_text(config)
    return tmp_path


# ---- the point of the feature --------------------------------------------

def test_stock_sqlfluff_destroys_the_alignment(tmp_path):
    """The problem, stated as a test. Without this the next one proves nothing."""
    formatted = format_sql(SQL, "postgres").text + "\n"
    box = _sandbox(tmp_path, formatted)
    _run(["fix", "--dialect", "postgres", "--force", "q.sql"], box)
    assert (box / "q.sql").read_text() != formatted, "stock sqlfluff left it alone"


def test_the_generated_config_makes_fix_a_no_op(tmp_path):
    formatted = format_sql(SQL, "postgres").text + "\n"
    box = _sandbox(tmp_path, formatted, sqlfluff_config(HOUSE, "postgres"))
    _run(["fix", "--force", "q.sql"], box)
    assert (box / "q.sql").read_text() == formatted


def test_the_generated_config_lints_clean(tmp_path):
    formatted = format_sql(SQL, "postgres").text + "\n"
    box = _sandbox(tmp_path, formatted, sqlfluff_config(HOUSE, "postgres"))
    result = _run(["lint", "q.sql"], box)
    assert result.returncode == 0, result.stdout or result.stderr


@pytest.mark.parametrize("preset", ["house", "compact", "trailing", "dbt", "gitlab", "river"])
def test_every_preset_lints_clean_under_its_own_config(tmp_path, preset):
    """A preset's output must satisfy the config that preset generates —
    otherwise picking a preset silently breaks the team's linter."""
    style = preset_style(preset)
    formatted = format_sql(SQL, "postgres", style).text + "\n"
    box = _sandbox(tmp_path, formatted, sqlfluff_config(style, "postgres"))
    result = _run(["lint", "q.sql"], box)
    assert result.returncode == 0, result.stdout or result.stderr


@pytest.mark.parametrize("sid", [s for s in SAMPLES if DIALECTS.get(s, "postgres") == "postgres"])
def test_no_golden_produces_a_layout_finding(tmp_path, sid):
    """The config's contract is narrower than "lints clean", and deliberately so.

    Semantic findings on the goldens are EXPECTED — the fixtures mix cast forms
    and GROUP BY reference styles on purpose, and one creates an index without
    CONCURRENTLY. Those are real observations about the SQL, which sqlalign
    preserves rather than rewrites, so the linter should keep making them. What
    must never appear is a LAYOUT finding: that would mean sqlfluff and sqlalign
    disagree about whitespace, which is the thing this config exists to settle.
    """
    box = _sandbox(tmp_path, load_pair(sid)[1] + "\n", sqlfluff_config(HOUSE, "postgres"))
    out = _run(["lint", "q.sql"], box).stdout
    layout_hits = [ln for ln in out.split("\n") if "[layout." in ln]
    assert not layout_hits, "\n".join(layout_hits)


# ---- the config is derived, not hardcoded --------------------------------

def test_keyword_case_reaches_the_capitalisation_rules():
    lower = sqlfluff_config(Style(keyword_case="lower"), "postgres")
    assert "capitalisation_policy = lower" in lower
    assert "extended_capitalisation_policy = lower" in lower
    assert "upper" not in lower


def test_table_alias_style_reaches_aliasing_table():
    """The default (bare) fails a stock sqlfluff install on every joined query,
    because AL01 defaults to `explicit`. This is the mapping that fixes it."""
    assert "aliasing = implicit" in sqlfluff_config(Style(table_alias_style="bare"), "postgres")
    assert "aliasing = explicit" in sqlfluff_config(Style(table_alias_style="as"), "postgres")


def test_neq_style_reaches_convention_not_equal():
    c_style = sqlfluff_config(Style(neq_style="!="), "postgres")
    ansi = sqlfluff_config(Style(neq_style="<>"), "postgres")
    assert "preferred_not_equal_style = c_style" in c_style
    assert "preferred_not_equal_style = ansi" in ansi


@pytest.mark.parametrize("dialect", ["postgres", "redshift", "tsql"])
def test_dialect_is_carried_through(dialect):
    assert f"dialect = {dialect}" in sqlfluff_config(HOUSE, dialect)


def test_a_derived_mapping_actually_binds(tmp_path):
    """Not just present in the text — sqlfluff must act on it. Lowercase output
    linted under an upper-case config has to FAIL, or the mapping is decorative."""
    lower_output = format_sql(SQL, "postgres", Style(keyword_case="lower")).text + "\n"
    box = _sandbox(tmp_path, lower_output, sqlfluff_config(HOUSE, "postgres"))
    assert _run(["lint", "q.sql"], box).returncode != 0


# ---- what it deliberately does NOT do ------------------------------------

def test_semantic_rules_stay_on(tmp_path):
    """sqlalign silencing the linter's semantic rules would quietly narrow what
    the team is allowed to notice. Only layout is excluded."""
    config = sqlfluff_config(HOUSE, "postgres")
    assert "exclude_rules = layout" in config
    for semantic in ("ambiguous.column_references", "aliasing.expression",
                     "convention.casting_style", "structure.join_condition_order"):
        assert semantic not in config

    # and prove it: a real semantic finding still fires on formatted output
    joined = format_sql(
        "select 1 from customers cust join orders ord on ord.customer_id = cust.customer_id;",
        "postgres").text + "\n"
    box = _sandbox(tmp_path, joined, config)
    result = _run(["lint", "q.sql"], box)
    assert "ST09" in result.stdout, result.stdout


def test_the_whole_layout_group_is_excluded_by_name_not_by_rule():
    """Naming individual rules dates instantly — a sqlfluff upgrade that adds a
    layout rule would start failing formatted output."""
    config = sqlfluff_config(HOUSE, "postgres")
    assert "exclude_rules = layout" in config
    for rule in ("layout.spacing", "layout.indent", "layout.commas", "LT01", "LT02"):
        assert rule not in config


def test_it_stamps_the_sqlfluff_version_it_was_checked_against():
    assert TESTED_SQLFLUFF in sqlfluff_config(HOUSE, "postgres")


def test_the_mappings_are_not_behind_the_installed_sqlfluff():
    """The mappings are only verified against `TESTED_SQLFLUFF`, and the tests
    above are what verify them. A newer series installed means nothing has
    checked its rule names, so this failing is the alarm doing its job: run the
    suite against that release, then bump `TESTED_SQLFLUFF`.

    The series, not the patch — the tests above run the real linter, and a patch
    release cannot move a rule name past them.
    """
    import sqlfluff

    from sqlalign.lint import _series
    assert _series(sqlfluff.__version__) <= _series(TESTED_SQLFLUFF), (
        f"sqlfluff is {sqlfluff.__version__}, mappings were checked against "
        f"{TESTED_SQLFLUFF} — re-verify them and bump TESTED_SQLFLUFF")


def test_the_checked_version_is_a_readable_version():
    """`version_warning` warns on anything it cannot compare, so a typo here
    would warn every user rather than nobody."""
    from sqlalign.lint import _series
    assert _series(TESTED_SQLFLUFF) is not None


# ---- CLI -----------------------------------------------------------------

def test_cli_runs_without_a_file(capsys):
    assert main(["--print-sqlfluff-config"]) == 0
    assert "exclude_rules = layout" in capsys.readouterr().out


def test_cli_reflects_a_discovered_config_file(tmp_path, capsys):
    (tmp_path / ".sqlalign.toml").write_text('keyword_case = "lower"\n')
    (tmp_path / "q.sql").write_text("select 1;\n")
    assert main(["--print-sqlfluff-config", str(tmp_path / "q.sql")]) == 0
    assert "capitalisation_policy = lower" in capsys.readouterr().out


def test_cli_reflects_a_preset(capsys):
    assert main(["--print-sqlfluff-config", "--preset", "dbt"]) == 0
    assert "capitalisation_policy = lower" in capsys.readouterr().out


def test_files_are_still_required_for_every_other_mode(capsys):
    with pytest.raises(SystemExit):
        main([])
