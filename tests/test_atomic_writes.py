"""Rewriting a file in place, and what happens when that goes wrong.

sqlalign's default mode rewrites the files it is given, so the failure that
matters most is not a bad layout — it is a run that destroys an input without
producing an output. `open(path, "w")` truncates before it writes, which leaves
exactly that window open: interrupt a run over a repository and some file is a
prefix of its formatted self, with the original gone.

These tests interrupt the real write path rather than asserting on the helper,
because the property being claimed is about what survives on disk.
"""
import os
import pathlib
import stat

import pytest

from sqlalign.cli import main

ORIGINAL = "select a, b, c, d from important_table where x = 1;\n" * 50


def _sql(tmp_path, name="q.sql", text=ORIGINAL):
    path = tmp_path / name
    path.write_text(text)
    return path


def _interrupt_the_write(monkeypatch, after=0.3):
    """Make the next write to the temp file stop part way through."""
    real_fdopen = os.fdopen

    def flaky(fd, *args, **kwargs):
        handle = real_fdopen(fd, *args, **kwargs)
        real_write = handle.write

        def partial(text):
            real_write(text[: int(len(text) * after)])
            raise KeyboardInterrupt("interrupted mid-write")

        handle.write = partial
        return handle

    monkeypatch.setattr(os, "fdopen", flaky)


def test_an_interrupted_write_leaves_the_original_untouched(tmp_path, monkeypatch):
    """The point of the whole module. Before this, the file was truncated to
    whatever had been flushed and the input was unrecoverable."""
    path = _sql(tmp_path)
    _interrupt_the_write(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        main([str(path)])

    assert path.read_text() == ORIGINAL


def test_an_interrupted_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    """A crash must not litter the user's tree with half-written neighbours."""
    path = _sql(tmp_path)
    _interrupt_the_write(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        main([str(path)])

    assert [p.name for p in tmp_path.iterdir()] == [path.name]


def test_a_symlink_is_written_through_rather_than_replaced(tmp_path):
    """Renaming into place would swap the link for a regular file, quietly
    detaching it from whatever it pointed at."""
    target = _sql(tmp_path, "real.sql")
    link = tmp_path / "link.sql"
    link.symlink_to(target)

    assert main([str(link)]) == 0
    assert link.is_symlink(), "the symlink was replaced by a regular file"
    assert target.read_text().startswith("SELECT")


def test_a_symlink_into_another_directory_still_works(tmp_path):
    """The temp file has to be created beside the TARGET, not beside the link,
    or the rename crosses a directory and -- in the general case -- a
    filesystem."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    target = _sql(tmp_path / "elsewhere", "target.sql")
    link = tmp_path / "link.sql"
    link.symlink_to(target)

    assert main([str(link)]) == 0
    assert link.is_symlink()
    assert target.read_text().startswith("SELECT")


def test_the_file_keeps_its_permissions(tmp_path):
    """A new inode starts with the process umask, so the mode has to be carried
    across explicitly -- otherwise formatting a file quietly widens it."""
    path = _sql(tmp_path)
    path.chmod(0o640)

    assert main([str(path)]) == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_a_read_only_file_is_reported_and_skipped(tmp_path, capsys):
    """`os.replace` only needs a writable directory, so without an explicit
    check a read-only file would be overwritten -- the opposite of what the
    plain write did. It must still refuse, and say so the documented way.
    """
    path = _sql(tmp_path)
    path.chmod(0o444)
    try:
        assert main([str(path)]) == 2
        assert path.read_text() == ORIGINAL
        assert "sqlalign:" in capsys.readouterr().err
    finally:
        path.chmod(0o644)


def test_one_unwritable_file_does_not_abort_the_run(tmp_path, capsys):
    """The documented contract: a per-file failure is reported and skipped, the
    rest still process, and the worst code seen is returned. Previously this
    raised PermissionError out of main and the remaining files never ran."""
    blocked = _sql(tmp_path, "blocked.sql")
    blocked.chmod(0o444)
    fine = _sql(tmp_path, "fine.sql")

    try:
        assert main([str(blocked), str(fine)]) == 2
        assert blocked.read_text() == ORIGINAL
        assert fine.read_text().startswith("SELECT"), "the run stopped at the first failure"
    finally:
        blocked.chmod(0o644)


def test_an_unwritable_directory_names_the_file_the_user_named(tmp_path, capsys):
    """The temp file is an implementation detail. Reporting `Permission denied:
    .q.sql.ab12cd.tmp` sends someone looking for a file they never created."""
    path = _sql(tmp_path)
    tmp_path.chmod(0o555)
    try:
        assert main([str(path)]) == 2
        error = capsys.readouterr().err
        assert str(path) in error, error
        assert ".tmp" not in error, error
        assert path.read_text() == ORIGINAL
    finally:
        tmp_path.chmod(0o755)


def test_the_ordinary_write_still_formats(tmp_path):
    """The boring case, so a regression in the machinery above is not mistaken
    for a formatting change."""
    path = _sql(tmp_path)
    assert main([str(path)]) == 0
    text = path.read_text()
    assert text.startswith("SELECT a")
    assert main([str(path)]) == 0
    assert path.read_text() == text, "not idempotent through the write path"


def test_a_directory_run_writes_every_file(tmp_path):
    nested = tmp_path / "models"
    nested.mkdir()
    paths = [_sql(tmp_path, "a.sql"), _sql(tmp_path / "models", "b.sql")]

    assert main([str(tmp_path)]) == 0
    for path in paths:
        assert path.read_text().startswith("SELECT")
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(pathlib.Path(tmp_path).rglob(".*.tmp"))
