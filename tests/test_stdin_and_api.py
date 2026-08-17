"""Reading from stdin, and the supported Python entry points.

Both exist for the same reason: sqlalign is useful in places that are not a
shell loop over files. `sqlalign -` is what an editor's format-on-save runs
through a generic external-formatter setting, and `sqlalign.format` is what a
notebook, a dbt hook or a code generator calls.
"""
import io
import subprocess
import sys
from types import SimpleNamespace

import pytest

import sqlalign
from sqlalign.cli import build_parser, main
from sqlalign.style import preset_style

MESSY = "select a,b from t;\n"
TIDY = "SELECT a\n     , b\nFROM t;\n"


def _run(args, stdin="", *, monkeypatch=None, capsys=None):
    """Drive `main` in process with `stdin` piped in.

    Not a subprocess: the tests would then depend on which interpreter resolves
    first, and this suite is run under several. `main` is the same code the
    console script calls.
    """
    raw = io.BytesIO(stdin.encode())
    monkeypatch.setattr(sys, "stdin",
                        SimpleNamespace(buffer=raw, read=lambda: stdin))
    code = main(args)
    captured = capsys.readouterr()
    return SimpleNamespace(returncode=code, stdout=captured.out, stderr=captured.err)


# ---- stdin -----------------------------------------------------------------

def test_a_bare_dash_formats_stdin_to_stdout(monkeypatch, capsys):
    """The default for `-` is to write the result, because there is no file to
    rewrite. This is the format-on-save path."""
    result = _run(["-"], MESSY, monkeypatch=monkeypatch, capsys=capsys)
    assert result.returncode == 0, result.stderr
    assert result.stdout == TIDY


def test_check_on_stdin_reports_instead_of_writing(monkeypatch, capsys):
    """--check has to win over the `-` default, or a gate would silently become
    a formatter that printed its answer."""
    result = _run(["--check", "-"], MESSY, monkeypatch=monkeypatch, capsys=capsys)
    assert result.returncode == 1
    assert result.stdout == "" or "would reformat" in result.stdout


def test_check_on_already_formatted_stdin_passes(monkeypatch, capsys):
    assert _run(["--check", "-"], TIDY, monkeypatch=monkeypatch, capsys=capsys).returncode == 0


def test_diff_on_stdin_prints_a_diff(monkeypatch, capsys):
    result = _run(["--diff", "-"], MESSY, monkeypatch=monkeypatch, capsys=capsys)
    assert result.returncode == 1
    assert result.stdout.startswith("--- -")


def test_stdin_respects_the_dialect_and_style_flags(monkeypatch, capsys):
    result = _run(["-", "--preset", "compact"], MESSY, monkeypatch=monkeypatch, capsys=capsys)
    assert result.returncode == 0
    assert result.stdout == sqlalign.format(MESSY, style=preset_style("compact"))


def test_crlf_survives_stdin(monkeypatch, capsys):
    """Read through sys.stdin rather than its buffer and Python's universal
    newline translation would rewrite the endings before anything noticed."""
    result = _run(["-"], "select a,b from t;\r\n", monkeypatch=monkeypatch, capsys=capsys)
    assert "\r\n" in result.stdout, repr(result.stdout)


def test_dash_cannot_be_combined_with_files(tmp_path, monkeypatch, capsys):
    """stdin is read once, so this has no sensible reading -- it would either
    format one input twice or silently drop the others."""
    sql = tmp_path / "q.sql"
    sql.write_text(MESSY)
    # argparse's `error` exits rather than returning, which is the same thing a
    # bad flag does.
    with pytest.raises(SystemExit) as exit_info:
        _run(["-", str(sql)], MESSY, monkeypatch=monkeypatch, capsys=capsys)
    assert exit_info.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_dash_is_documented_in_the_cli_reference():
    """`-` is discoverable only if it is written down; nothing about the parser
    hints that a positional argument has a special value."""
    from pathlib import Path
    guide = Path(__file__).resolve().parent.parent / "docs" / "guide" / "cli.md"
    assert "`-`" in guide.read_text()


# ---- the Python API --------------------------------------------------------

def test_format_returns_the_text():
    assert sqlalign.format(MESSY) == TIDY


def test_format_result_reports_what_happened():
    """The reason both exist: `format` cannot tell you a statement was passed
    through, and passing through is normal rather than exceptional."""
    result = sqlalign.format_result(
        "select a from t; select * from t pivot (sum(x) for y in ('a'));")
    assert result.statements == 2
    assert any(d.kind == "unsupported" for d in result.declines)


def test_a_style_can_be_passed():
    """A join, because the presets agree on a two-column SELECT -- what differs
    is the FROM block, so the sample has to reach it."""
    sql = "select a, b from customers c join orders o on o.cid = c.id;"
    assert sqlalign.format(sql, style=preset_style("compact")) != sqlalign.format(sql)


def test_an_unsupported_dialect_raises_rather_than_guessing():
    """The handlers emit keywords chosen for the verified dialects, and the AST
    check cannot catch output that is valid SQL but invalid for the engine."""
    with pytest.raises(ValueError, match="unsupported dialect"):
        sqlalign.format("select 1;", dialect="mysql")


def test_the_public_surface_is_declared():
    assert set(sqlalign.__all__) == {"format", "format_result"}


def test_importing_sqlalign_does_not_pull_in_sqlglot():
    """The entry points defer their imports so `import sqlalign` stays cheap.
    Startup is most of the wall time of a small run, and a caller reading
    `__version__` should not pay for the parser."""
    probe = ("import sys; import sqlalign; "
             "print('sqlglot' in sys.modules)")
    result = subprocess.run([sys.executable, "-c", probe],
                            capture_output=True, text=True)
    assert result.stdout.strip() == "False", "sqlglot is imported eagerly again"


def test_version_is_still_readable():
    assert sqlalign.__version__
    with pytest.raises(AttributeError):
        _ = sqlalign.no_such_attribute


def test_the_dash_argument_parses():
    assert build_parser().parse_args(["-"]).files == ["-"]


def test_main_is_importable_for_embedding():
    assert callable(main)


def test_the_editor_docs_do_not_still_deny_stdin(tmp_path, monkeypatch, capsys):
    """The guide said "sqlalign has no stdin mode" and taught a `/dev/stdin`
    workaround with an explicit `--config`, for weeks after `-` shipped. A
    feature nobody is told about is one nobody has.
    """
    from pathlib import Path

    guide = (Path(__file__).resolve().parent.parent
             / "docs" / "guide" / "getting-started.md").read_text()
    assert "no stdin mode" not in guide
    assert ":%!sqlalign -" in guide, "the filter recipe should use `-`"


def test_dash_reads_the_config_in_the_working_directory(tmp_path, monkeypatch, capsys):
    """The property the guide now claims, and the reason `-` beats `/dev/stdin`
    for a buffer filter: config discovery walks up from the path you named, and
    `/dev/stdin` is not in anyone's repository."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sqlalign.toml").write_text('keyword_case = "lower"\n')
    result = _run(["-"], MESSY, monkeypatch=monkeypatch, capsys=capsys)
    assert result.stdout.startswith("select a"), result.stdout


def test_lone_cr_stdin_echoes_the_input(monkeypatch, capsys):
    """The lone-CR passthrough predates `-` and only knew --stdout, so `-`
    printed the warning and emitted NOTHING with exit 0. Both documented editor
    integrations replace the buffer with stdout, so the user's SQL became an
    empty file and nothing signalled failure."""
    source = "select a from t where s = 'x\ry';\n"
    result = _run(["-"], source, monkeypatch=monkeypatch, capsys=capsys)
    assert result.returncode == 0
    assert result.stdout == source, "passthrough must mean echo, never silence"
    assert "lone CR" in result.stderr


def test_a_closed_reader_is_not_a_traceback(monkeypatch, capsys):
    """`sqlalign - | head` closes stdout early. `console` maps the resulting
    BrokenPipeError to 141 -- how a shell spells death-by-SIGPIPE -- instead of
    dumping a traceback; `main` stays exception-transparent for embedders."""
    from sqlalign import cli

    monkeypatch.setattr(cli, "main", lambda argv=None: (_ for _ in ()).throw(BrokenPipeError()))
    assert cli.console() == 141


def test_crlf_survives_the_api_without_a_decline():
    """The CLI normalized CRLF before formatting; the API did not, so the same
    file that formatted at the command line declined through `format_result`
    with `comment recovery mismatch` -- a second, subtly different tool."""
    result = sqlalign.format_result("select a, b -- keep\r\nfrom t;\r\n")
    assert not result.declines, result.declines
    assert "\r\n" in result.text
    assert "-- keep" in result.text


def test_lone_cr_through_the_api_is_a_warned_passthrough():
    source = "select a\rfrom t;"
    result = sqlalign.format_result(source)
    assert result.text == source
    assert any("lone CR" in w for w in result.warnings)


def test_a_templated_file_still_counts_statements_and_declines():
    """The templating branch rebuilt FormatResult from text and warnings alone,
    so statements fell to 0 and declines to () -- which blinded --report and
    --max-declines for every templated file, most of a dbt project."""
    result = sqlalign.format_result(
        "select {{ ref('m') }}.a from t; "
        "select * from t pivot (sum(x) for y in (1));")
    assert result.statements == 2
    assert any(d.kind == "unsupported" for d in result.declines)


def test_unmaskable_templating_is_a_counted_decline():
    """A passthrough --report cannot see is invisible in exactly the way
    --report exists to prevent."""
    result = sqlalign.format_result("select a from {% if x %}t{% else %}")
    if result.warnings and "not maskable" in result.warnings[0]:
        assert result.declines, "warned but not counted"
