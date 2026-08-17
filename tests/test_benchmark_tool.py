"""`tools/benchmark.py`, the tool behind the numbers in the FAQ.

Timings are not asserted -- a CI runner's wall clock is shared with whatever
else is on the machine, and a performance test that fails when the runner is
busy trains people to ignore it. What is asserted is that the tool still runs
and still reports, so the published figures stay reproducible rather than
becoming a claim nobody can check.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_the_benchmark_runs_and_reports():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "benchmark.py"), "--files", "12"],
        capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    for expected in ("--check", "rewrite", "startup", "projected"):
        assert expected in proc.stdout, f"{expected!r} missing from:\n{proc.stdout}"


def test_the_faq_points_at_the_tool():
    """The numbers are only trustworthy while a reader can regenerate them."""
    faq = (ROOT / "docs" / "guide" / "faq.md").read_text()
    assert "tools/benchmark.py" in faq
