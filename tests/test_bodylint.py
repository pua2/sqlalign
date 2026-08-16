"""Linting inside a `$$ … $$` body.

sqlfluff cannot do it. To its parser a plpgsql body is one string literal, so a
function full of badly-written SQL passes clean — which is a real hole in any
repository that keeps logic in functions, and it is not a rule sqlfluff is
missing but the shape of its parse.

sqlalign already has to find those statements in order to format them, so it can
hand them to a linter too. `bodylint.lint_view` returns the file with everything
that is not lintable SQL replaced by SPACES: the plpgsql scaffolding, the CREATE
header, the dollar quotes. Blanking rather than deleting is the whole trick —
delete and every column after it shifts, so a finding would point at the wrong
place. This way every line and column survives and a reported position is a
position in the real file.
"""
import pytest

from sqlalign import bodylint

FUNCTION = """CREATE FUNCTION refresh() RETURNS int AS $$
BEGIN

select    a,b   from    orders where x=1;

DELETE   FROM   staging   WHERE   done;

RETURN 1;

END;
$$ LANGUAGE plpgsql;
"""

MIXED = """select   x   from   top_level;

CREATE FUNCTION one() RETURNS int AS $$
BEGIN
  insert  into  audit ( a )  values ( 1 );
  RETURN 1;
END;
$$ LANGUAGE plpgsql;

update   other   set   y = 2;
"""


# ---- the view ------------------------------------------------------------

def test_positions_survive():
    """The property everything else depends on: a finding's line and column in
    the view are its line and column in the source."""
    view = bodylint.lint_view(FUNCTION)
    assert len(view) == len(FUNCTION), "length changed, so columns moved"
    assert view.count("\n") == FUNCTION.count("\n"), "line count changed"
    for a, b in zip(FUNCTION.split("\n"), view.split("\n"), strict=True):
        assert len(a) == len(b), f"line width changed: {a!r} -> {b!r}"


def test_the_body_sql_is_what_survives():
    kept = [ln for ln in bodylint.lint_view(FUNCTION).split("\n") if ln.strip()]
    assert kept == ["select    a,b   from    orders where x=1;",
                    "DELETE   FROM   staging   WHERE   done;"]


def test_the_scaffolding_is_gone():
    """CREATE, BEGIN, RETURN, END and the dollar quotes are not SQL and would
    stop the linter parsing the view at all."""
    view = bodylint.lint_view(FUNCTION)
    for scaffolding in ("CREATE", "BEGIN", "RETURN", "END", "$$", "LANGUAGE"):
        assert scaffolding not in view, f"{scaffolding} survived into the view"


def test_statements_outside_a_body_are_untouched():
    """A linter can already see those. Rewriting them risks changing what it
    says about them, for no gain."""
    view = bodylint.lint_view(MIXED)
    assert "select   x   from   top_level;" in view
    assert "update   other   set   y = 2;" in view
    assert "insert  into  audit ( a )  values ( 1 );" in view


def test_several_functions_in_one_file():
    source = MIXED + "\n" + FUNCTION
    kept = [ln.strip() for ln in bodylint.lint_view(source).split("\n") if ln.strip()]
    assert "insert  into  audit ( a )  values ( 1 );" in kept
    assert "DELETE   FROM   staging   WHERE   done;" in kept


def test_a_file_with_no_body_is_returned_unchanged():
    plain = "select a from t;\nupdate u set b = 1;\n"
    assert bodylint.lint_view(plain) == plain
    assert not bodylint.has_bodies(plain)


# ---- the line bookkeeping ------------------------------------------------

def test_body_lines_names_only_lines_the_view_can_see():
    """The caller keeps the view's findings ONLY on these lines, which is what
    makes the two lint runs disjoint."""
    assert bodylint.body_lines(FUNCTION) == {2, 3, 4, 5, 6}


def test_body_lines_excludes_top_level_statements():
    """A statement piece carries the blank line before it, so a span starts on
    the PREVIOUS statement's line. Deriving these from the whole CREATE
    statement claimed line 1 — a top-level statement — as body content, which
    would have reported its findings twice.
    """
    lines = bodylint.body_lines(MIXED)
    assert 1 not in lines, "the top-level SELECT was claimed as body content"
    assert 10 not in lines, "the top-level UPDATE was claimed as body content"
    assert 5 in lines, "the body's INSERT is not covered"


def test_artifact_lines_are_the_padding():
    """Blanking leaves whitespace-only lines the source never had, so a
    whitespace rule fires on them. They are identifiable, not guessed at."""
    artifacts = bodylint.artifact_lines(FUNCTION)
    assert 1 in artifacts and 10 in artifacts       # CREATE, END
    assert 4 not in artifacts and 6 not in artifacts  # the two real statements


# ---- end to end, against the real linter ---------------------------------

sqlfluff = pytest.importorskip("sqlfluff")


def _lint(tmp_path, source, name="q.sql"):
    from sqlalign.lint import lint
    from sqlalign.style import HOUSE

    path = tmp_path / name
    path.write_text(source)
    return lint(path, source, HOUSE, "postgres")


def test_sqlfluff_alone_really_is_blind_to_bodies(tmp_path):
    """The premise, asserted rather than assumed. If sqlfluff ever learns to
    read a plpgsql body, this fails and the whole module can be revisited."""
    import subprocess
    import sys

    path = tmp_path / "fn.sql"
    path.write_text(FUNCTION)
    out = subprocess.run([sys.executable, "-m", "sqlfluff", "lint",
                          "--dialect", "postgres", str(path)],
                         capture_output=True, text=True).stdout
    assert "L:   4" not in out, "sqlfluff now reads inside $$; revisit bodylint"
    assert "L:   6" not in out


def test_a_finding_inside_a_body_is_reported(tmp_path):
    body = ("CREATE FUNCTION report() RETURNS int AS $$\n"
            "BEGIN\n"
            "\n"
            "SELECT * FROM orders o, customers c WHERE o.cid = c.id;\n"
            "\n"
            "RETURN 1;\n"
            "\n"
            "END;\n"
            "$$ LANGUAGE plpgsql;\n")
    _code, out, _err = _lint(tmp_path, body)
    assert "inside $$ bodies" in out
    assert "AM04" in out or "RF02" in out, out


def test_the_reported_line_is_the_real_line(tmp_path):
    """A finding that points at the wrong line is worse than no finding."""
    body = ("CREATE FUNCTION report() RETURNS int AS $$\n"   # 1
            "BEGIN\n"                                        # 2
            "\n"                                             # 3
            "\n"                                             # 4
            "SELECT * FROM orders o, customers c WHERE o.cid = c.id;\n"  # 5
            "\n"
            "RETURN 1;\n"
            "\n"
            "END;\n"
            "$$ LANGUAGE plpgsql;\n")
    _code, out, _err = _lint(tmp_path, body)
    section = out[out.index("inside $$ bodies"):]
    assert "L:   5" in section, section


def test_a_clean_body_reports_nothing(tmp_path):
    clean = ("CREATE FUNCTION f() RETURNS int AS $$\n"
             "BEGIN\n"
             "\n"
             "DELETE FROM staging\n"
             "WHERE done;\n"
             "\n"
             "RETURN 1;\n"
             "\n"
             "END;\n"
             "$$ LANGUAGE plpgsql;\n")
    _code, out, _err = _lint(tmp_path, clean)
    assert "inside $$ bodies" not in out, out


def test_a_file_with_no_body_is_unaffected(tmp_path):
    _code, out, _err = _lint(tmp_path, "SELECT a\nFROM t;\n")
    assert "inside $$ bodies" not in out
