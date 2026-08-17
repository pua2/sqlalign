"""The comment half of the safety guarantee.

Every statement is re-parsed and AST-compared before it is written. That check
cannot see comments — sqlglot hangs a comment off the token it precedes rather
than putting it in the tree, so:

    ast_equal("SELECT a -- keep me", "SELECT a", "postgres")  ->  True

A statement and the same statement with its comment deleted are identical to it.
Every comment bug this project has had was therefore invisible to the one
mechanism meant to catch changes in meaning, and two of them changed meaning:
`SELECT a -- c,` lost a separator, and `SELECT a -- note;` left the statement
unterminated so it swallowed the next one.

`comments_equal` closes that. These tests check the oracle itself, and then
check that a renderer which drops a comment is actually stopped by it — the
second is what makes it a guarantee rather than a helper function.
"""
import pytest

from sqlalign import formatter
from sqlalign.formatter import ast_equal, comment_text, comments_equal, format_sql

# ---- the gap this exists to close -----------------------------------------

def test_the_ast_check_cannot_see_comments():
    """Stated as a test so the reason this module exists cannot quietly stop
    being true. If sqlglot ever puts comments in the tree, this fails and the
    separate check becomes redundant."""
    assert ast_equal("SELECT a -- keep me\n, b FROM t", "SELECT a, b FROM t", "postgres")
    assert ast_equal("SELECT a /* block */ FROM t", "SELECT a FROM t", "postgres")


# ---- the oracle ------------------------------------------------------------

def test_comments_are_extracted_in_source_order():
    text = comment_text("-- one\nSELECT a, b -- two\nFROM t; -- three", "postgres")
    assert text == ["one", "two", "three"]


def test_block_and_line_comments_are_both_seen():
    assert comment_text("SELECT /* b */ a -- l", "postgres") == ["b", "l"]


@pytest.mark.parametrize("before,after,equal", [
    ("SELECT a -- note", "SELECT a -- note", True),
    # Position is not compared: the layout deliberately moves a comment to the
    # end of the row above, so where it sits is expected to change.
    ("SELECT a -- note\n, b", "SELECT a -- note\n     , b", True),
    ("SELECT a -- note", "SELECT a", False),                    # dropped
    ("SELECT a -- note", "SELECT a -- note\n-- note", False),   # duplicated
    ("SELECT a -- note", "SELECT a -- other", False),           # rewritten
    ("SELECT a -- one\n, b -- two", "SELECT a -- two\n, b -- one", False),  # reordered
])
def test_what_the_oracle_treats_as_equal(before, after, equal):
    assert comments_equal(before, after, "postgres") is equal


# ---- the guarantee ---------------------------------------------------------

def _drop_comments(text):
    return "\n".join(line.split("--")[0].rstrip() for line in text.splitlines())


def test_a_renderer_that_drops_a_comment_is_stopped(monkeypatch):
    """The load-bearing test. A layout engine that loses a comment must have the
    statement passed through byte-identical, not written out short.

    Simulated by making the renderer drop comments, because the real engine does
    not — which is the point: this is the net under the trapeze, and a net is
    only proven by falling into it.
    """
    real = formatter._format_statement

    def lossy(stmt, dialect, style):
        return _drop_comments(real(stmt, dialect, style))

    monkeypatch.setattr(formatter, "_format_statement", lossy)

    source = "select a, b -- keep me\nfrom t;"
    result = format_sql(source, "postgres")

    assert result.text == source, "the statement was not passed through unchanged"
    assert any("would change a comment" in w for w in result.warnings), result.warnings


def test_the_decline_is_reported_as_its_own_cause(monkeypatch):
    """`--report` ranks causes, so losing a comment and changing an expression
    have to be distinguishable. Both are renderer bugs; they are not the same
    bug and should not be counted together."""
    real = formatter._format_statement
    monkeypatch.setattr(formatter, "_format_statement",
                        lambda s, d, st: _drop_comments(real(s, d, st)))

    result = format_sql("select a, b -- keep me\nfrom t;", "postgres")
    reasons = [d.reason for d in result.declines]
    assert "output would change a comment" in reasons
    assert "output would differ semantically" not in reasons


def test_ordinary_commented_sql_still_formats():
    """The check must not cost real statements their formatting: a false
    positive here is a file that silently stops being formatted."""
    result = format_sql("select a, b -- keep me\nfrom t where x = 1;", "postgres")
    assert not result.warnings, result.warnings
    assert result.text.startswith("SELECT a")
    assert "-- keep me" in result.text


def test_input_the_tokenizer_rejects_is_reported_as_unverified():
    """`comments_equal` compares by tokenizing, so input the tokenizer refuses
    has no answer. Refusing is the safe direction: the statement is passed
    through rather than written on the strength of a check that did not run.

    Unreachable through `format_sql` — such input fails to parse and declines
    earlier — but the helper is importable, so it should not raise at a caller.
    """
    assert comments_equal("SELECT a /* unterminated", "SELECT a", "postgres") is False
