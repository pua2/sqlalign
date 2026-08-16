"""Three bugs in areas the construct sweeps had not covered.

Both wore the same disguise — `formatting would change semantics`, the wording
that means the RENDERER is wrong rather than the construct unsupported — and
both were found by sweeping a corner nobody had swept: comments, and T-SQL
procedure bodies.

**A dropped column.** A LINE comment runs to the end of its line, so anything
after it on that line is inside it. The select-list layout emitted a leading
comment inline, the way it correctly emits a block comment:

    select a, -- note
      b from t;          ->   SELECT a
                              , -- note b        <- `b` is INSIDE the comment

The column was simply gone. `ast_equal` caught the loss and reported it as a
semantic change, so a dropped column read as an unsupported construct.

**A doubled semicolon.** The T-SQL BEGIN/END layout closes a procedure with
`END;` — the house adds it even where the source wrote a bare `END`, which is
what the golden pins. The statement emitter then appended the source's own
terminator on top, giving `END;;`, which re-parses differently. So a procedure
whose source DID write `END;` declined. This one was already known: the guide
called it "one sharp edge" and documented it rather than fixing it.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql


def fmt(sql, dialect="postgres"):
    result = format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


def _fixed_point(sql, dialect="postgres", rounds=3):
    """Format repeatedly; every pass after the first must be identical."""
    outs, text = [], sql
    for _ in range(rounds):
        text = fmt(text, dialect)
        outs.append(text)
    assert len(set(outs)) == 1, f"not idempotent: {outs}"
    return outs[0]


# ---- the line comment that ate a column -----------------------------------

def test_a_leading_line_comment_does_not_swallow_its_item():
    sql = "select a, -- note\n b from t;"
    out = fmt(sql)
    assert out == "SELECT a -- note\n     , b\nFROM t;"
    assert ast_equal(sql, out, "postgres")


def test_the_column_really_was_being_lost():
    """The premise, asserted directly rather than trusted: the broken spelling
    is not merely ugly, it means something different."""
    assert not ast_equal("select a, b from t;", "SELECT a\n     , -- note b\nFROM t;",
                         "postgres")


def test_it_lands_where_a_second_pass_would_put_it():
    """The placement is forced, not chosen. A line comment on its own row above
    `, b` re-parses as TRAILING the row before, so that spelling moves on a
    second run — and idempotence is not negotiable."""
    assert _fixed_point("select a, -- note\n b from t;") == (
        "SELECT a -- note\n     , b\nFROM t;")


def test_several_line_comments():
    assert _fixed_point("select a, -- one\n b, -- two\n c from t;") == (
        "SELECT a -- one\n     , b -- two\n     , c\nFROM t;")


def test_a_comment_before_the_first_item():
    """Nothing precedes it, so it takes the row above SELECT — at the
    statement's column, not the item column, which would leave it in mid-air."""
    assert _fixed_point("select -- first\n a, b from t;") == (
        "-- first\nSELECT a\n     , b\nFROM t;")


def test_a_block_comment_still_rides_the_row():
    """Only LINE comments have the end-of-line problem. The block form is what
    golden 12 pins, and it must not have moved."""
    assert fmt("select a, /* block */ b from t;") == (
        "SELECT a\n     , /* block */ b\nFROM t;")


def test_golden_12_is_unchanged():
    """The comment engine's own golden — leading block comment inline, trailing
    line comment at end of row."""
    inp, expected = load_pair("12")
    assert format_sql(inp, "postgres").text == expected


# ---- the doubled semicolon ------------------------------------------------

@pytest.mark.parametrize("source_end", ["end", "end;"])
def test_a_procedure_formats_either_way(source_end):
    """It used to decline whenever the source wrote `END;`."""
    sql = f"create procedure p as begin select 1; {source_end}"
    out = fmt(sql, "tsql")
    assert out == "CREATE PROCEDURE p\nAS\nBEGIN\n\nSELECT 1;\n\nEND;"
    assert ast_equal(sql, out, "tsql")


def test_the_parameterised_form_too():
    out = fmt("create procedure p @x int as begin select @x; end;", "tsql")
    assert out.startswith("CREATE PROCEDURE p @x INTEGER\nAS\nBEGIN\n")
    assert out.endswith("END;")


def test_the_house_adds_the_terminator_even_without_one_in_the_source():
    """That is why the layout owns this semicolon rather than the emitter — it
    is part of the BEGIN/END shape, and golden 28's source ends in a bare
    `end`."""
    assert fmt("create procedure p as begin select 1; end", "tsql").endswith("END;")


def test_an_ordinary_statement_still_follows_its_source():
    """The fix must not have made every statement terminate itself."""
    assert fmt("select a from t;", "tsql").endswith("FROM t;")
    assert fmt("select a from t", "tsql").endswith("FROM t")


def test_golden_28_is_unchanged():
    inp, expected = load_pair("28")
    assert format_sql(inp, "tsql").text == expected


# ---- invariants -----------------------------------------------------------

SHAPES = [
    ("postgres", "select a, -- note\n b from t;"),
    ("postgres", "select -- first\n a, b from t;"),
    ("postgres", "select a, /* block */ b from t;"),
    ("tsql", "create procedure p as begin select 1; end;"),
    ("tsql", "create procedure p @x int as begin select @x; end;"),
]


@pytest.mark.parametrize("dialect,sql", SHAPES)
def test_semantics_and_idempotence(dialect, sql):
    out = _fixed_point(sql, dialect)
    assert ast_equal(sql, out, dialect)


@pytest.mark.parametrize("dialect,sql", SHAPES)
def test_none_of_them_decline(dialect, sql):
    assert format_sql(sql, dialect).warnings == []


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected
