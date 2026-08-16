"""CRLF handling (`--line-ending`).

Before this, the CLI refused to format any file containing a carriage return —
which meant no Windows-checkout shop could adopt sqlalign at all. CRLF files are
now normalized to LF for the engine and restored on the way out, so the file's
own convention survives. A lone CR (classic-Mac) is still passed through: it is
not a line ending this tool models, and silently rewriting it would be worse than
declining.
"""
from sqlalign.cli import main


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_bytes(text.encode())
    return path


def _read(path):
    return path.read_bytes().decode()


MESSY = "select a,b from t where x = 1 and y = 2;\n"


def test_crlf_file_is_formatted_and_stays_crlf(tmp_path):
    path = _write(tmp_path, "q.sql", MESSY.replace("\n", "\r\n"))
    assert main([str(path)]) == 0
    out = _read(path)
    assert "\r\n" in out                       # convention preserved
    assert "\n" not in out.replace("\r\n", "")  # no bare LF left behind
    assert "SELECT" in out                      # and it actually formatted


def test_lf_file_stays_lf(tmp_path):
    path = _write(tmp_path, "q.sql", MESSY)
    assert main([str(path)]) == 0
    assert "\r" not in _read(path)


def test_crlf_already_formatted_reports_no_diff(tmp_path):
    """--check on a formatted CRLF file must exit 0, not report every line as
    changed because of the carriage returns."""
    path = _write(tmp_path, "q.sql", MESSY)
    main([str(path)])                                  # format it (LF)
    formatted_lf = _read(path)
    crlf = _write(tmp_path, "crlf.sql", formatted_lf.replace("\n", "\r\n"))
    assert main(["--check", str(crlf)]) == 0
    assert _read(crlf) == formatted_lf.replace("\n", "\r\n")   # untouched


def test_line_ending_flag_forces_conversion(tmp_path):
    path = _write(tmp_path, "q.sql", MESSY)
    assert main(["--line-ending", "crlf", str(path)]) == 0
    assert "\r\n" in _read(path)

    back = _write(tmp_path, "b.sql", MESSY.replace("\n", "\r\n"))
    assert main(["--line-ending", "lf", str(back)]) == 0
    assert "\r" not in _read(back)


def test_lone_cr_still_passes_through(tmp_path):
    original = MESSY.replace("\n", "\r")
    path = _write(tmp_path, "old_mac.sql", original)
    assert main([str(path)]) == 0
    assert _read(path) == original             # byte-identical, never rewritten


def test_no_align_flag(tmp_path):
    path = _write(tmp_path, "q.sql",
                  "select a from t inner join u on u.id = t.id and u.k = t.k;\n")
    assert main(["--no-align", str(path)]) == 0
    out = _read(path)
    for line in out.split("\n"):
        assert "  " not in line.lstrip(" "), line
