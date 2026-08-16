"""`Style.blank_lines_between_statements`.

Unset (the house rule) means exactly one blank line between two MULTI-LINE
statements and none otherwise, so a run of one-liners stays a block while two long
queries get air between them. An integer forces that count between every pair.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.cli import main
from sqlalign.configfile import build_style, describe, load_settings
from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style

TWO_LONG = "select a, b from t where x = 1;\nselect c from u where y = 2;\n"
ONE_LINERS = "truncate table a;\ntruncate table b;\ntruncate table c;\n"


def _blank_runs(text):
    """Number of blank lines at each gap between non-blank blocks."""
    runs, count = [], 0
    for line in text.split("\n"):
        if line.strip():
            if count:
                runs.append(count)
            count = 0
        else:
            count += 1
    return runs


# ---- the house rule is unchanged -------------------------------------------

@pytest.mark.parametrize("sid", SAMPLES)
def test_unset_reproduces_every_golden(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres"), Style()).text == expected


def test_unset_puts_one_blank_between_two_multiline_statements():
    assert _blank_runs(format_sql(TWO_LONG, "postgres").text) == [1]


def test_unset_keeps_one_liners_adjacent():
    assert _blank_runs(format_sql(ONE_LINERS, "postgres").text) == []


# ---- forcing a count -------------------------------------------------------

@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_forced_count_applies_between_multiline_statements(n):
    out = format_sql(TWO_LONG, "postgres", Style(blank_lines_between_statements=n)).text
    assert _blank_runs(out) == ([n] if n else [])


@pytest.mark.parametrize("n", [1, 2])
def test_forced_count_also_separates_one_liners(n):
    """The point of forcing: shape no longer decides."""
    out = format_sql(ONE_LINERS, "postgres", Style(blank_lines_between_statements=n)).text
    assert _blank_runs(out) == [n, n]


def test_forced_zero_removes_the_house_blank_line():
    out = format_sql(TWO_LONG, "postgres", Style(blank_lines_between_statements=0)).text
    assert "\n\n" not in out


# ---- invariants ------------------------------------------------------------

@pytest.mark.parametrize("n", [None, 0, 1, 2])
@pytest.mark.parametrize("sid", ["11", "23", "13"])
def test_semantics_and_idempotency_hold(sid, n):
    inp = load_pair(sid)[0]
    style = Style(blank_lines_between_statements=n)
    out = format_sql(inp, "postgres", style).text
    assert ast_equal(inp, out, "postgres")
    assert format_sql(out, "postgres", style).text == out


# ---- validation and plumbing ----------------------------------------------

@pytest.mark.parametrize("bad", [-1, "one", 1.5, True])
def test_invalid_values_rejected(bad):
    with pytest.raises(ValueError):
        Style(blank_lines_between_statements=bad)


def test_default_is_unset():
    assert Style().blank_lines_between_statements is None


def test_config_file_sets_it(tmp_path):
    (tmp_path / ".sqlalign.toml").write_text("blank_lines_between_statements = 2\n")
    sql = tmp_path / "q.sql"
    sql.write_text(TWO_LONG)
    assert main([str(sql)]) == 0
    assert _blank_runs(sql.read_text()) == [2]


def test_cli_flag_sets_it(tmp_path):
    sql = tmp_path / "q.sql"
    sql.write_text(ONE_LINERS)
    assert main(["--blank-lines-between-statements", "1", str(sql)]) == 0
    assert _blank_runs(sql.read_text()) == [1, 1]


def test_show_config_output_stays_loadable_when_unset(tmp_path):
    """An unset value has no TOML spelling, so it must be emitted commented out
    rather than as `= # unset`, which is not valid TOML."""
    path = tmp_path / ".sqlalign.toml"
    path.write_text(describe(Style()))
    assert build_style(load_settings(path)[0]) == Style()
