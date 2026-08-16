"""`Style.table_alias_style` — `FROM orders o` (house) vs `FROM orders AS o`.

The third distinction sqlglot's parser destroys: `FROM t AS a` and `FROM t a`
produce an identical AST, so the printed spelling is sqlalign's choice and not
the author's. That also means the safety net is BLIND here — `ast_equal` compares
the two as equal — so these tests are the only thing standing between a wrong
spelling and a silent rewrite of every table reference in a repo.

sqlalign is not neutral and cannot be: a COLUMN alias always prints `AS`
(`SELECT x foo` → `SELECT x AS foo`), because that spelling is collapsed too.
Table aliases defaulted the other way. `table_alias_style` makes the table half
configurable without moving the default, which would rewrite existing output.

Five call sites print a table alias — FROM, JOIN, a derived table, and the DML
verbs — and the risk is that they drift apart, so every one is covered here.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import HOUSE, Style

AS = Style(table_alias_style="as")


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- every site that prints a table alias --------------------------------

@pytest.mark.parametrize("sql,bare,with_as", [
    ("select 1 from orders o;",
     "FROM orders o", "FROM orders AS o"),
    ("select 1 from a x join b y on y.id = x.id;",
     "JOIN b y ON", "JOIN b AS y ON"),
    ("update orders as o set n = 1;",
     "UPDATE orders o", "UPDATE orders AS o"),
    ("delete from orders as o where o.id = 1;",
     "DELETE FROM orders o", "DELETE FROM orders AS o"),
    ("select 1 from t x join (select a from u) as d on d.a = x.a;",
     ") d ON", ") AS d ON"),
])
def test_both_spellings_at_every_site(sql, bare, with_as):
    assert bare in fmt(sql)
    assert with_as in fmt(sql, AS)


def test_the_input_spelling_does_not_leak_into_the_output():
    """Both inputs collapse to the same AST, so both must print identically —
    the knob decides, never the source text."""
    assert fmt("select 1 from t as a;") == fmt("select 1 from t a;")
    assert fmt("select 1 from t as a;", AS) == fmt("select 1 from t a;", AS)


# ---- the safety net cannot see this, so pin it explicitly ----------------

def test_ast_equal_is_blind_to_the_spelling():
    """Documents WHY this file exists: the re-parse guard passes either way, so
    it would not catch a regression here."""
    assert ast_equal("select 1 from t a;", "select 1 from t AS a;", "postgres")


@pytest.mark.parametrize("sql", [
    "select 1 from orders o;",
    "select 1 from a x join b y on y.id = x.id;",
    "update orders as o set n = 1;",
])
def test_output_still_means_the_input(sql):
    assert ast_equal(sql, fmt(sql, AS), "postgres")


# ---- composition and defaults --------------------------------------------

def test_the_knob_only_adds_as_tokens():
    """Strip every `AS` from both spellings and the token streams are identical:
    the knob inserts a keyword and touches nothing else. (Column widths DO shift,
    because `AS o` is wider than `o` and the alias column is measured from the
    text — that is alignment doing its job, not the knob overreaching.)"""
    sql = "select 1 from a x join b y on y.id = x.id and y.k = 'z';"

    def tokens(text):
        return [t for t in text.split() if t != "AS"]

    assert tokens(fmt(sql, AS)) == tokens(fmt(sql))
    assert fmt(sql, AS).count("AS") == fmt(sql).count("AS") + 2   # one per table


def test_column_aliases_are_unaffected():
    """The knob is scoped to TABLE aliases; a column alias prints AS either way."""
    for style in (None, AS):
        assert "x AS foo" in fmt("select x foo from t;", style)


def test_composes_with_align_off():
    out = fmt("select 1 from a x join b y on y.id = x.id;",
              Style(table_alias_style="as", align=False))
    assert out == "SELECT 1\nFROM a AS x\nJOIN b AS y ON y.id = x.id;"


def test_composes_with_lowercase_keywords():
    out = fmt("select 1 from orders o;", Style(table_alias_style="as", keyword_case="lower"))
    assert "from orders as o" in out


def test_house_default_is_bare():
    assert HOUSE.table_alias_style == "bare"


# ---- quoted aliases keep their quotes ------------------------------------

@pytest.mark.parametrize("alias", ['"My Alias"', '"x"', '"X"'])
def test_quoted_aliases_survive(alias):
    """Reading the alias off `.name` returned it UNQUOTED, so `FROM a "My Alias"`
    came out as bare `My Alias` — a different identifier. Nothing declined it
    explicitly; the re-parse guard noticed the quoting had changed and passed the
    statement through, which is a silent decline behind the safety net where the
    house rule is to decline deliberately or not at all. Rendering the identifier
    keeps the quotes, so these format instead."""
    sql = f'select 1 from a {alias} join b y on y.id = {alias}.id;'
    out = fmt(sql)
    assert f"FROM a {alias}" in out
    assert f"FROM a AS {alias}" in fmt(sql, AS)
    assert ast_equal(sql, out, "postgres")


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged_by_the_default(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


def test_idempotent():
    once = fmt("select 1 from a x join b y on y.id = x.id;", AS)
    assert fmt(once, AS) == once


@pytest.mark.parametrize("bad", ["AS", "As", "none", "", True])
def test_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        Style(table_alias_style=bad)


# ---- the flag is actually wired (nothing else checks this) ---------------

def test_config_and_cli(tmp_path):
    from sqlalign.cli import main

    sql = tmp_path / "q.sql"
    sql.write_text("select 1 from orders o;\n")
    assert main(["--table-alias-style", "as", str(sql)]) == 0
    assert "FROM orders AS o" in sql.read_text()

    (tmp_path / ".sqlalign.toml").write_text('table_alias_style = "bare"\n')
    sql.write_text("select 1 from orders o;\n")
    assert main([str(sql)]) == 0
    assert "FROM orders o" in sql.read_text()
