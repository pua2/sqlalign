"""`--report` and `--max-declines`: making a passthrough visible.

A decline is safe but INVISIBLE — it warns on stderr and exits 0, so a CI run
stays green with any fraction of a repository unformatted:

    $ sqlalign --check models/     # 2 of 5 statements never touched
    exit=0

That is the gap this closes. The counts also turn the remaining unmodelled
constructs into a ranked list measured on the team's own SQL, rather than a
guess about what to implement next — which is worth more than any single
construct, and is exactly how the user-defined-function bug would have been
caught on day one instead of by accident.
"""
import pytest

from sqlalign.cli import main
from sqlalign.formatter import format_sql

MIXED = (
    "select a from orders;\n"
    "select * from t pivot (sum(x) for y in (1, 2)) p;\n"
    "select c from customers;\n"
    "select * from t unpivot (x for y in (a, b)) u;\n"
    "select e from events;\n"
)


# The same statements, already formatted — so `--check` is satisfied and a test
# about the DECLINE gate is not also testing the reformat exit code.
SETTLED = format_sql(MIXED, "postgres").text + "\n"


def _file(tmp_path, text=MIXED, name="q.sql"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


# ---- the counts themselves -----------------------------------------------

def test_format_sql_counts_statements_and_declines():
    result = format_sql(MIXED, "postgres")
    assert result.statements == 5
    assert len(result.declines) == 2


def test_a_decline_carries_the_reason_not_just_the_sql():
    """The Unsupported message used to be discarded, which left a report able to
    say how many statements declined but not WHICH construct to go implement."""
    result = format_sql("select * from t pivot (sum(x) for y in (1, 2)) p;", "postgres")
    assert [d.kind for d in result.declines] == ["unsupported"]
    assert result.declines[0].reason, "no reason recorded"
    assert any("PIVOT" in w for w in result.warnings), result.warnings


@pytest.mark.parametrize("sql,kind", [
    ("select * from t pivot (sum(x) for y in (1, 2)) p;", "unsupported"),
    ("this is not sql at all (((;", "parse"),
    ("-- sqlalign: skip\nselect a   from t;", "skipped"),
])
def test_decline_kinds(sql, kind):
    assert [d.kind for d in format_sql(sql, "postgres").declines] == [kind]


def test_an_authors_skip_is_distinguishable_from_a_gap():
    """`-- sqlalign: skip` IS a statement sqlalign did not format, so it counts —
    but as its own kind, so a report can tell a deliberate opt-out from a hole in
    the tool."""
    result = format_sql("-- sqlalign: skip\nselect a from t;", "postgres")
    assert result.statements == 1
    assert result.declines[0].kind == "skipped"


def test_a_fully_formatted_file_has_no_declines():
    result = format_sql("select a from t;\nselect b from u;\n", "postgres")
    assert result.statements == 2
    assert result.declines == ()


# ---- the CLI report ------------------------------------------------------

def test_report_shows_the_ratio(tmp_path, capsys):
    main(["--report", "--check", _file(tmp_path)])
    out = capsys.readouterr().out
    assert "5 statements" in out
    assert "3 formatted (60.0%)" in out
    assert "2 declined" in out


def test_report_ranks_the_causes(tmp_path, capsys):
    main(["--report", "--check", _file(tmp_path, MIXED + MIXED)])
    out = capsys.readouterr().out
    assert "declined by cause" in out
    body = out[out.index("declined by cause"):]
    counts = [int(line.split()[0]) for line in body.split("\n")[1:] if line.strip()]
    assert counts == sorted(counts, reverse=True), body


def test_report_on_a_clean_file(tmp_path, capsys):
    main(["--report", "--check", _file(tmp_path, "select a from t;\n")])
    out = capsys.readouterr().out
    assert "1 formatted (100.0%)" in out
    assert "declined by cause" not in out


def test_report_aggregates_across_files(tmp_path, capsys):
    a = _file(tmp_path, MIXED, "a.sql")
    b = _file(tmp_path, MIXED, "b.sql")
    main(["--report", "--check", a, b])
    assert "10 statements" in capsys.readouterr().out


def test_report_does_not_suppress_formatting(tmp_path):
    """It is a summary, not a mode — the file is still written."""
    path = tmp_path / "q.sql"
    path.write_text("select a   from t;\n")
    main(["--report", str(path)])
    assert path.read_text() == "SELECT a\nFROM t;\n"


# ---- the gate ------------------------------------------------------------

def test_max_declines_fails_when_exceeded(tmp_path, capsys):
    assert main(["--check", "--max-declines", "0", _file(tmp_path)]) == 1
    assert "over the --max-declines limit of 0" in capsys.readouterr().err


def test_max_declines_passes_when_within_budget(tmp_path):
    assert main(["--check", "--max-declines", "5", _file(tmp_path, SETTLED)]) == 0


def test_max_declines_implies_the_report(tmp_path, capsys):
    main(["--check", "--max-declines", "9", _file(tmp_path)])
    assert "statements" in capsys.readouterr().out


def test_without_the_gate_declines_do_not_fail(tmp_path):
    """The existing contract: a passthrough is not a failure. Only the new flag
    changes that, and only when asked."""
    assert main(["--check", _file(tmp_path, SETTLED)]) == 0


def test_the_gate_composes_with_check(tmp_path):
    """An unformatted file exits 1 for --check; the gate must not mask that."""
    path = tmp_path / "q.sql"
    path.write_text("select a   from t;\n")
    assert main(["--check", "--max-declines", "99", str(path)]) == 1
