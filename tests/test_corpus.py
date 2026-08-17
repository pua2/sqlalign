"""The formatter run over SQL nobody on this project wrote.

Fixtures written alongside a formatter test what its author already thought of.
This corpus is third-party — sqlglot's own parser fixtures, dbt's canonical
example project, and a macro-heavy dbt package — vendored at pinned commits
under `tests/corpus/`. See that directory's README for sources and licences.

It earned its place immediately: the first run found two real bugs that every
hand-written fixture had missed.

  * `WITH "cte" AS (...)` declined. The CTE name was printed from `.alias`,
    which strips quoting, so the output named a different relation and the
    re-parse guard rejected it. sqlglot's optimizer quotes every identifier it
    emits, so all of its output tripped this — as would any tool that generates
    SQL the same way.
  * `WHERE NOT b IS NULL` declined under Postgres. The renderer rewrote it to
    `b IS NOT NULL`: the same meaning, but not what the author wrote, and the
    guard was right to reject it.

What is asserted is deliberately narrow. Declining is allowed — the corpus is
full of constructs and dialects sqlalign does not model, and passing those
through is the contract. What is not allowed is a crash, a decline that means
the renderer is wrong, or output that changes on a second pass.
"""
import pathlib

import pytest

from sqlalign.formatter import format_sql

CORPUS = pathlib.Path(__file__).resolve().parent / "corpus"
FILES = sorted(CORPUS.rglob("*.sql"))
DIALECTS = ("postgres", "redshift", "tsql")

# A decline whose cause is one of these means the layout produced output that
# does not mean what the input meant. That is a bug in sqlalign, not a construct
# nobody implemented, and `tests/test_no_silent_declines.py` says the same thing
# about hand-written SQL.
RENDERER_BUGS = ("output would differ semantically", "output would change a comment")


def _cases():
    return [(f, d) for f in FILES for d in DIALECTS]


def _id(case):
    return f"{case[0].parent.name}/{case[0].name}-{case[1]}"


def test_the_corpus_is_present():
    """A corpus that quietly became empty would turn every test below green."""
    assert len(FILES) >= 40, f"only {len(FILES)} corpus files; run tools/fetch_corpus.py"
    assert (CORPUS / "README.md").exists(), "corpus attribution is missing"


@pytest.mark.parametrize("case", _cases(), ids=_id)
def test_no_statement_crashes_the_formatter(case):
    """An internal error is always a bug: the file still formats around it, but
    the statement is passed through for a reason nobody chose."""
    path, dialect = case
    result = format_sql(path.read_text(errors="replace"), dialect)
    errors = [d for d in result.declines if d.kind == "error"]
    assert not errors, [d.reason for d in errors]


@pytest.mark.parametrize("case", _cases(), ids=_id)
def test_no_decline_blames_the_renderer(case):
    """Unsupported and upstream declines are fine and expected here. A safety
    decline is not: it means the layout wrote something that does not mean what
    it was given."""
    path, dialect = case
    result = format_sql(path.read_text(errors="replace"), dialect)
    bugs = [d.reason for d in result.declines if d.reason in RENDERER_BUGS]
    assert not bugs, bugs


@pytest.mark.parametrize("case", _cases(), ids=_id)
def test_formatting_reaches_a_fixed_point(case):
    """`format(format(x)) == format(x)`. Several layout bugs in this project
    were visible only on the second pass, because the first pass moved content
    onto a line the second pass then read differently."""
    path, dialect = case
    once = format_sql(path.read_text(errors="replace"), dialect).text
    twice = format_sql(once, dialect).text
    assert once == twice


@pytest.mark.parametrize("case", _cases(), ids=_id)
def test_every_comment_survives(case):
    """The engine enforces this per statement, so this is the file-level version:
    it also covers comments between statements, which no single statement owns.
    """
    from sqlglot.errors import TokenError

    from sqlalign.formatter import comment_text

    path, dialect = case
    source = path.read_text(errors="replace")
    try:
        before = comment_text(source, dialect)
    except TokenError:
        # A whole-file tokenize can fail where each statement tokenizes fine --
        # this corpus holds SQL for other dialects, including an unterminated
        # block comment. Such a file cannot be compared this way, and it is the
        # comparison that is unavailable, not the property that is broken.
        pytest.skip("the file as a whole does not tokenize in this dialect")
    assert before == comment_text(format_sql(source, dialect).text, dialect)
