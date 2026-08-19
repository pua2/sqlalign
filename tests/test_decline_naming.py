"""What a decline calls the statement it passed through, and REVOKE's absence.

Three things a real permissions-and-procedure script hit at once, all of them
about a statement sqlglot's parser handles specially:

  * `SET ROLE reporting` and `RESET ROLE` were reported as
    `unsupported construct (Command)`. `Command` is sqlglot's catch-all for
    unparsed syntax -- it names the fallback, not the statement, and is the same
    word for every unrelated construct that lands there. (`SET search_path TO
    public` read as `Set` for the same reason; it now formats, cased from its
    source, so only the Commands are left to name.)
  * sqlglot logs its own fallback to stderr, unprefixed, next to sqlalign's line
    about the same statement. Two messages, the worse one first.
  * `REVOKE` declined while the `GRANT` above it formatted. Not a modelling gap:
    it parses to its own node and renders on one line exactly as GRANT does, it
    was simply missing from the dispatch.
"""
import logging

import pytest
import sqlglot
from sqlglot import exp

from sqlalign.cli import main
from sqlalign.formatter import format_sql
from sqlalign.layout import construct_name


@pytest.mark.parametrize(("sql", "name"), [
    pytest.param("SET ROLE reporting;", "SET", id="Command-SET"),
    pytest.param("RESET ROLE;", "RESET", id="Command-RESET"),
    pytest.param("SET search_path TO reporting, public;", "SET", id="Command-SET-list"),
    pytest.param("VACUUM ANALYZE t;", "VACUUM", id="Command-VACUUM"),
])
def test_an_undeclined_statement_is_named_in_sql_not_in_python(sql, name):
    result = format_sql(sql, "postgres")
    assert [d.reason for d in result.declines] == [name]
    assert result.text == sql, "not passed through byte-identical"


def test_a_class_name_that_is_not_sql_is_left_alone():
    """The keyword check is against the dialect's own vocabulary rather than a
    list here, so it uppercases `Set` and leaves alone a name that merely looks
    like a keyword."""
    assert construct_name(exp.Set(), "postgres") == "SET"
    assert construct_name(exp.Semicolon(), "postgres") == "Semicolon"


@pytest.mark.parametrize(("sql", "dialect"), [
    ("SET search_path TO public", "postgres"),      # `TO` respelt as `=`
    ("SET LOCAL work_mem = '64MB'", "tsql"),        # `LOCAL` dropped
    ("SET SESSION AUTHORIZATION bob", "tsql"),      # `SESSION` dropped
])
def test_set_is_never_handed_to_the_generator(sql, dialect):
    """`exp.Set` parses, so it could be rendered from its node -- and must not
    be. That generator respells `SET x TO y` as `SET x = y`, and under T-SQL
    drops `LOCAL` and `SESSION` outright. SET is cased from its source instead.

    The sentinel: if one of these starts round-tripping, the node becomes a
    legitimate thing to render from and this decision is worth revisiting.
    """
    assert sqlglot.parse_one(sql, dialect=dialect).sql(dialect=dialect) != sql, (
        f"sqlglot no longer rewrites {sql!r} under {dialect}")
    assert format_sql(sql + ";", dialect).text.strip() == sql + ";", (
        "SET no longer comes out as written")


@pytest.mark.parametrize("sql", [
    "revoke select on t from bob",
    "revoke all on schema s from grp cascade",
    "revoke usage on schema reporting from group analysts",
])
def test_revoke_formats_the_way_grant_does(sql):
    result = format_sql(sql + ";", "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert result.text.startswith("REVOKE "), result.text
    assert result.text.count("\n") == 0, "REVOKE is a one-line statement"


def test_grant_and_revoke_agree(tmp_path):
    """The asymmetry itself: the pair belongs in a script together, and one of
    them formatting while the other passed through is what made it visible."""
    path = tmp_path / "perms.sql"
    path.write_text("grant select on t to bob;\nrevoke select on t from bob;\n")
    assert main([str(path)]) == 0
    assert path.read_text() == "GRANT SELECT ON t TO bob;\nREVOKE SELECT ON t FROM bob;\n"


def test_the_cli_does_not_repeat_sqlglots_fallback_warning(tmp_path, capsys):
    """sqlalign's own line names the statement and shows its text; sqlglot's says
    the same thing about an internal of its parser."""
    path = tmp_path / "role.sql"
    path.write_text("SET ROLE reporting;\n")
    logging.getLogger("sqlglot").setLevel(logging.WARNING)   # undo any earlier run
    main(["--check", str(path)])
    err = capsys.readouterr().err
    assert "unsupported construct (SET)" in err
    assert "Falling back to parsing" not in err, "sqlglot is still narrating on our stderr"


def test_the_library_leaves_sqlglots_logger_alone():
    """Silencing it is the CLI's call about its own output. `sqlalign.format` runs
    inside someone else's program, where that is not its business."""
    logging.getLogger("sqlglot").setLevel(logging.WARNING)
    format_sql("SET ROLE reporting;", "postgres")
    assert logging.getLogger("sqlglot").level == logging.WARNING
