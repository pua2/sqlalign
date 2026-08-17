"""Quoted column aliases — the same bug `table_alias` documents, one node type
over and found much later.

sqlglot's `Alias.alias` returns the identifier's NAME, with the quoting stripped:

    select revenue as "Total Revenue" from t;   -- rendered `AS Total Revenue`

which is a syntax error, so the re-parse guard rejected the output and passed the
statement through untouched. **Every quoted column alias in every repository**,
which is ordinary reporting SQL, and it was invisible: a silent decline behind
the safety net, where the house rule is to decline explicitly or not at all.

Quoting is not cosmetic here — `"b"` and `b` are different columns in Postgres —
so the guard was right to reject the output. The renderer was the thing that was
wrong. It now goes through `column_alias`, the sibling of the `table_alias`
helper whose docstring described this exact failure for the other node type.

It was found by feeding the GUI deliberately awkward input and noticing that
`select "café" as "naïve" from t` declined. The unicode was a red herring —
plain ASCII `"cafe" as "naive"` declined just as hard.
"""
import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.style import Style, preset_style


def fmt(sql, style=None, dialect="postgres"):
    result = format_sql(sql, dialect, style) if style else format_sql(sql, dialect)
    assert result.warnings == [], f"declined: {result.warnings}"
    return result.text


# ---- the alias survives ---------------------------------------------------

@pytest.mark.parametrize("sql,expect", [
    ('select a as "b" from t;', 'SELECT a AS "b"'),
    ('select revenue as "Total Revenue" from t;', 'SELECT revenue AS "Total Revenue"'),
    ('select t.a as "b" from t;', 'SELECT t.a AS "b"'),
    ('select sum(x) as "Total" from t;', 'SELECT SUM(x) AS "Total"'),
    ('select "a" as "b" from t;', 'SELECT "a" AS "b"'),
])
def test_a_quoted_alias_keeps_its_quotes(sql, expect):
    assert fmt(sql).startswith(expect)


@pytest.mark.parametrize("alias", ['"Total Revenue"', '"café"', '"日本語"', '"Row #"',
                                   '"select"', '"a.b"', '"has ""quote"""'])
def test_the_spellings_that_need_the_quotes(alias):
    """Each of these is a syntax error or a different identifier unquoted."""
    sql = f"select x as {alias} from t;"
    assert fmt(sql) == f"SELECT x AS {alias}\nFROM t;"


def test_a_quoted_alias_is_not_casefolded():
    """Keyword case applies to keywords. A quoted identifier is the one place
    the author's exact spelling is load-bearing."""
    out = fmt('select a as "MixedCase" from t;', Style(keyword_case="lower"))
    assert out == 'select a as "MixedCase"\nfrom t;'


def test_an_unquoted_alias_is_unaffected():
    assert fmt("select a as b from t;") == "SELECT a AS b\nFROM t;"


# ---- every path that prints one -------------------------------------------

def test_a_case_item_alias():
    """`_multiline_item_lines` — the alias lands on the body's last line."""
    assert fmt('select case when x then 1 else 2 end as "My Col" from t;') == (
        "SELECT CASE WHEN x THEN 1\n"
        "            ELSE 2\n"
        '       END AS "My Col"\n'
        "FROM t;"
    )


def test_a_window_item_alias():
    assert fmt('select row_number() over (partition by a order by b) as "Row #" '
               "from t;").startswith(
        'SELECT ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) AS "Row #"')


def test_a_scalar_subquery_alias():
    """`_scalar_subquery_lines` — its own site, and it aligns with the plain
    item's `AS` below it."""
    assert fmt('select (select max(x) from u) as "Peak", b as plain from t;') == (
        'SELECT (SELECT MAX(x)\n'
        '        FROM u) AS "Peak"\n'
        '     , b        AS plain\n'
        "FROM t;"
    )


def test_quoted_and_bare_aliases_share_the_as_column():
    """The width measurement in expr.py reads the alias too; leaving that one
    site on `.alias` would have mis-measured every quoted item by two."""
    out = fmt('select sum(x) as "Total Revenue", b as plain, ccc as "Third" from t;')
    rows = [ln for ln in out.split("\n") if " AS " in ln]
    assert len({ln.index(" AS ") for ln in rows}) == 1, out


# ---- invariants -----------------------------------------------------------

SHAPES = [
    'select a as "b" from t;',
    'select revenue as "Total Revenue", cost as plain from t;',
    'select case when x then 1 else 2 end as "My Col" from t;',
    'select (select max(x) from u) as "Peak" from t;',
    'select row_number() over (order by b) as "Row #" from t;',
    'select "café" as "naïve" from "tëst";',
]


@pytest.mark.parametrize("sql", SHAPES)
def test_semantics_and_idempotence(sql):
    out = fmt(sql)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out) == out


@pytest.mark.parametrize("sql", SHAPES)
@pytest.mark.parametrize("preset", ["compact", "gitlab", "river", "dbt", "trailing"])
def test_they_compose_with_the_presets(sql, preset):
    style = preset_style(preset)
    out = fmt(sql, style)
    assert ast_equal(sql, out, "postgres")
    assert fmt(out, style) == out


@pytest.mark.parametrize("sid", SAMPLES)
def test_goldens_unchanged(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, DIALECTS.get(sid, "postgres")).text == expected


def test_the_guard_still_rejects_a_real_change():
    """Preserving the quotes must not blind the net to a quoting change that
    genuinely alters which column is meant."""
    assert not ast_equal('select a as "b" from t;', "select a as b from t;", "postgres")
    assert ast_equal('select a as "b" from t;', 'select a as "b" from t;', "postgres")


# ---- the third site: a CTE name --------------------------------------------
#
# Found by the corpus suite rather than by hand. sqlglot's optimizer quotes every
# identifier it emits, so all of its output tripped this -- as would any tool
# that generates SQL the same way.

@pytest.mark.parametrize("dialect,quoted", [("postgres", '"cte"'),
                                            ("redshift", '"cte"'),
                                            ("tsql", "[cte]")])
def test_a_quoted_cte_name_keeps_its_quotes(dialect, quoted):
    """`WITH "cte" AS` came out as bare `WITH cte AS`, which names a different
    relation in Postgres and is a syntax error whenever the name needs quoting.

    T-SQL spells the quoting `[cte]`, which is sqlglot rendering the identifier
    in the target dialect rather than the quoting being lost.
    """
    result = format_sql('WITH "cte" AS (SELECT a FROM x) SELECT a FROM "cte";', dialect)
    assert not result.warnings, result.warnings
    assert f"{quoted} AS (" in result.text, result.text


@pytest.mark.parametrize("name", ['"My CTE"', '"select"', '"MixedCase"', '"café"'])
def test_cte_names_that_only_survive_quoted(name):
    """Names that are a syntax error unquoted, so dropping the quotes could not
    round-trip even in principle."""
    result = format_sql(f"WITH {name} AS (SELECT a FROM x) SELECT a FROM {name};",
                        "postgres")
    assert not result.warnings, result.warnings
    assert f"{name} AS (" in result.text, result.text


def test_an_unquoted_cte_name_is_left_unquoted():
    """The fix must not start quoting names the author wrote bare."""
    result = format_sql("WITH cte AS (SELECT a FROM x) SELECT a FROM cte;", "postgres")
    assert "cte AS (" in result.text
    assert '"cte"' not in result.text
