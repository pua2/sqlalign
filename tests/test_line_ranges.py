"""Formatting a selection rather than a whole file.

The unit is the STATEMENT, not the line. Half a statement does not parse, so a
range that starts or ends inside one formats that statement entire -- which is
also what an editor selection means when someone drags across part of a query.

Everything outside the range comes back byte-identical, including the blank
lines around it: a caller who asked for line 12 did not ask for the spacing
above line 40 to be normalised. That is why an out-of-range statement is emitted
as trivia rather than as a statement the blank-line rule can move.

The use this earns its place for is adopting sqlalign on a repository nobody
wants to reformat in one commit: format the lines a change touches, leave the
rest of the file alone, and the diff stays reviewable.
"""
import pytest

from sqlalign.cli import main, parse_line_ranges
from sqlalign.formatter import format_sql

THREE = "select a,b from t;\n\nselect c,d from u;\n\nselect e,f from v;\n"


@pytest.mark.parametrize(("values", "expected"), [
    (["3:9"], ((3, 9),)),
    (["12"], ((12, 12),)),                       # a bare number is that one line
    (["1:2", "8:9"], ((1, 2), (8, 9))),
])
def test_ranges_parse(values, expected):
    assert parse_line_ranges(values) == expected


@pytest.mark.parametrize("value", ["0", "0:3", "9:2", "a:b", "", "3:"])
def test_a_bad_range_is_rejected(value):
    with pytest.raises(ValueError):
        parse_line_ranges([value])


def test_only_the_selected_statement_changes():
    result = format_sql(THREE, "postgres", lines=((3, 3),))
    assert result.text == ("select a,b from t;\n\n"
                           "SELECT c\n     , d\nFROM u;\n\n"
                           "select e,f from v;\n")


def test_ranges_are_repeatable():
    result = format_sql(THREE, "postgres", lines=((1, 1), (5, 5)))
    assert "SELECT a" in result.text and "SELECT e" in result.text
    assert "select c,d from u;" in result.text, "the middle statement was touched"


def test_a_range_that_starts_mid_statement_formats_the_whole_statement():
    """Half a statement does not parse, so the whole one is the smallest unit
    there is."""
    source = "select a,b from t;\n\nselect c,\n  d\nfrom u;\n"
    result = format_sql(source, "postgres", lines=((4, 4),))     # the `d` line
    assert "SELECT c\n     , d\nFROM u;" in result.text
    assert result.text.startswith("select a,b from t;")


def test_a_range_matching_nothing_leaves_the_file_alone():
    """Line 2 is the blank line between two statements."""
    result = format_sql(THREE, "postgres", lines=((2, 2),))
    assert result.text == THREE
    assert result.statements == 0, "a statement nobody selected was counted"


def test_only_selected_statements_are_counted():
    """`--report` and `--max-declines` count what sqlalign was asked to do, and
    a statement outside the range was not asked for."""
    assert format_sql(THREE, "postgres").statements == 3
    assert format_sql(THREE, "postgres", lines=((1, 1),)).statements == 1


def test_spacing_outside_the_range_is_not_normalised():
    """Three blank lines between two statements is not the house rule, and stays
    that way when nobody asked about those lines."""
    source = "select a,b from t;\n\n\n\nselect c,d from u;\n"
    result = format_sql(source, "postgres", lines=((5, 5),))
    assert result.text.startswith("select a,b from t;\n\n\n\n"), result.text


def test_it_is_a_fixed_point():
    once = format_sql(THREE, "postgres", lines=((3, 3),)).text
    assert format_sql(once, "postgres", lines=((3, 3),)).text == once


def test_the_cli_writes_only_the_range(tmp_path):
    path = tmp_path / "q.sql"
    path.write_text(THREE)
    assert main(["--lines", "1", str(path)]) == 0
    written = path.read_text()
    assert written.startswith("SELECT a")
    assert "select c,d from u;" in written and "select e,f from v;" in written


def test_check_reports_only_the_range(tmp_path):
    path = tmp_path / "q.sql"
    path.write_text(THREE)
    assert main(["--check", "--lines", "1", str(path)]) == 1
    assert main(["--check", "--lines", "2", str(path)]) == 0, (
        "a blank line selected a statement")
    assert path.read_text() == THREE, "--check wrote to the file"


@pytest.mark.parametrize("argv", [
    ["--lines", "1", "a.sql", "b.sql"],
    ["--lines", "1", "."],                       # a directory is many files
])
def test_a_range_across_many_files_is_refused(argv, tmp_path, capsys):
    """One range against many files would mean "line 12 of each"."""
    with pytest.raises(SystemExit):
        main(argv)
    assert "--lines applies to a single file" in capsys.readouterr().err
