import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).parent.parent / "src")


def run_cli(args, cwd, text=True):
    """`text=False` captures raw bytes, for assertions about the exact line-ending
    bytes on the pipe."""
    return subprocess.run(
        [sys.executable, "-m", "sqlalign.cli", *args],
        capture_output=True, text=text, cwd=cwd,
        env={**os.environ, "PYTHONPATH": SRC},
    )


def test_stdout_passthrough(tmp_path):
    f = tmp_path / "q.sql"
    f.write_text("SELECT 1;\n")
    r = run_cli(["--stdout", str(f)], tmp_path)
    assert r.returncode == 0
    assert r.stdout == "SELECT 1;\n"


def test_check_clean_exits_zero(tmp_path):
    f = tmp_path / "q.sql"
    f.write_text("SELECT 1;\n")
    assert run_cli(["--check", str(f)], tmp_path).returncode == 0


def test_missing_file_exits_two(tmp_path):
    assert run_cli(["--stdout", "nope.sql"], tmp_path).returncode == 2


def test_stdout_and_check_are_mutually_exclusive(tmp_path):
    f = tmp_path / "q.sql"
    f.write_text("SELECT 1;\n")
    assert run_cli(["--stdout", "--check", str(f)], tmp_path).returncode == 2


def test_crlf_file_stdout_keeps_crlf_bytes(tmp_path):
    """CRLF files are formatted (they used to be refused outright, which locked
    out every Windows checkout); the file's own line ending is restored on the
    way out, and no warning is emitted because nothing was declined."""
    f = tmp_path / "q.sql"
    f.write_bytes(b"select 1;\r\nselect 2;\r\n")
    # Raw byte capture (text=False) so the parent process performs no newline
    # translation of its own on the pipe — this proves the CRLF bytes reach the
    # far end of --stdout untouched, not just that Python's text layer agrees.
    r = run_cli(["--stdout", str(f)], tmp_path, text=False)
    assert r.returncode == 0
    assert r.stdout == b"SELECT 1;\r\nSELECT 2;\r\n"   # formatted, still CRLF
    assert b"\n" not in r.stdout.replace(b"\r\n", b"")  # no bare LF leaked out
    assert b"CRLF" not in r.stderr


def test_crlf_file_check_exits_zero(tmp_path):
    f = tmp_path / "q.sql"
    f.write_bytes(b"SELECT 1;\r\n")
    r = run_cli(["--check", str(f)], tmp_path)
    assert r.returncode == 0


def test_diff_prints_a_unified_diff_and_exits_one(tmp_path):
    f = tmp_path / "q.sql"
    f.write_text("select a,b from t;\n")
    r = run_cli(["--diff", str(f)], tmp_path)
    assert r.returncode == 1
    assert "--- " in r.stdout and "+++ " in r.stdout
    assert "+SELECT a" in r.stdout
    assert f.read_text() == "select a,b from t;\n"       # writes nothing


def test_diff_on_a_formatted_file_is_silent_and_exits_zero(tmp_path):
    f = tmp_path / "q.sql"
    f.write_text("SELECT a\n     , b\nFROM t;\n")
    r = run_cli(["--diff", str(f)], tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""


def test_check_names_the_file_without_printing_a_diff(tmp_path):
    """--check is for CI: actionable without burying the log in diffs. Use
    --diff to see the change itself."""
    f = tmp_path / "q.sql"
    f.write_text("select a,b from t;\n")
    r = run_cli(["--check", str(f)], tmp_path)
    assert r.returncode == 1
    assert "would reformat" in r.stdout
    assert "+SELECT" not in r.stdout
    assert f.read_text() == "select a,b from t;\n"


def test_diff_and_check_are_mutually_exclusive(tmp_path):
    f = tmp_path / "q.sql"
    f.write_text("SELECT 1;\n")
    assert run_cli(["--diff", "--check", str(f)], tmp_path).returncode == 2


def test_version_flag(capsys):
    """A released tool reports its version. Read from package metadata rather
    than a second copy in the source, so the two cannot disagree."""
    from importlib.metadata import version

    from sqlalign.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"sqlalign {version('sqlalign')}"


def test_the_package_carries_release_metadata():
    """PyPI renders these; a missing one is a blank field on the project page."""
    from importlib.metadata import metadata

    meta = metadata("sqlalign")
    for field in ("Summary", "License-Expression", "Requires-Python"):
        assert meta.get(field), f"pyproject is missing {field}"
    assert any("Homepage" in u or "Source" in u for u in meta.get_all("Project-URL") or [])
    assert meta.get("Summary") != "", "description is empty"


# ---- files sqlalign cannot decode -----------------------------------------
#
# The read is `path.open()`, which is UTF-8. A file in any other encoding raised
# UnicodeDecodeError -- a ValueError, so it slipped past the OSError handler and
# aborted the whole run with a traceback.

def _bytes_file(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return path


@pytest.mark.parametrize("name,raw", [
    ("latin1.sql", 'select "café" from t;\n'.encode("latin-1")),
    ("utf16.sql", "select a from t;\n".encode("utf-16")),
    ("binary.sql", bytes(range(256))),
])
def test_a_file_that_is_not_utf8_is_reported_and_left_alone(tmp_path, capsys, name, raw):
    """Guessing an encoding would mean writing the file back in a different
    one, which is the kind of change sqlalign exists not to make."""
    from sqlalign.cli import main

    path = _bytes_file(tmp_path, name, raw)
    assert main([str(path)]) == 2
    assert path.read_bytes() == raw, "the file was modified"
    assert "not valid UTF-8" in capsys.readouterr().err


def test_a_file_that_is_not_utf8_does_not_abort_the_run(tmp_path):
    """Previously the traceback took the whole invocation with it, so every
    file after the first undecodable one was silently never formatted."""
    from sqlalign.cli import main

    _bytes_file(tmp_path, "bad.sql", 'select "café" from t;\n'.encode("latin-1"))
    good = tmp_path / "good.sql"
    good.write_text("select a,b from t;\n")

    assert main([str(tmp_path)]) == 2
    assert good.read_text().startswith("SELECT a"), "the run stopped at the bad file"
