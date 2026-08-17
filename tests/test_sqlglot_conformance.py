"""The sqlglot AST shapes sqlalign reads, asserted one at a time.

The layout engine does not walk a generic tree. It dispatches on specific node
types, reads specific argument keys, and prints from specific properties. Those
are sqlglot's internals, and they move between releases.

**The safety net cannot catch it when they do.** `ast_equal` compares sqlalign's
output against its input under the *same* sqlglot, so an upstream shape change
moves both sides of the comparison together: the check stays green while the
layout quietly changes, or a construct silently starts declining. Only the
byte-exact goldens would notice — and a golden failure is several hundred bytes
of diff that does not say which assumption broke.

This module says it. Each assertion names the shape, what sqlalign does with it,
and what goes wrong if it changes. When a sqlglot bump turns one red, the failure
is a sentence rather than an archaeology exercise.

This is what makes the dependency range safe to widen: `>=30.14,<31` rather than
a single patch line, with CI running both ends.
"""
import sqlglot
from sqlglot import exp

DIALECT = "postgres"


def parse(sql: str, dialect: str = DIALECT):
    return sqlglot.parse_one(sql, dialect=dialect)


# ---- identifiers and quoting ----------------------------------------------
#
# The silent class. A quoting change does not raise; it prints a different
# identifier, which the re-parse guard then rejects, and the statement passes
# through with a decline nobody asked for.

def test_alias_strips_quoting_so_rendering_must_not_use_it():
    """`Alias.alias` returns the identifier's NAME, unquoted.

    Three sites were caught printing from it — table aliases, column aliases and
    CTE names — each shipping a silent decline until it was found. `column_alias`,
    `table_alias` and `cte_name` exist because of this, and would be pointless
    busywork if sqlglot ever started preserving the quotes here.
    """
    alias = parse('SELECT x AS "Total Revenue" FROM t').selects[0]
    assert alias.alias == "Total Revenue", "no longer strips quoting: the helpers can go"
    assert isinstance(alias.args["alias"], exp.Identifier), "alias is no longer an Identifier"
    assert alias.args["alias"].quoted is True, "quoting is no longer recorded on the Identifier"


def test_a_cte_name_is_a_table_alias_wrapping_an_identifier():
    """`cte_name` reaches through `args["alias"].this` for the name alone.
    Rendering the `TableAlias` itself would splice a column list back in."""
    cte = parse('WITH "c" AS (SELECT 1) SELECT 1').find(exp.CTE)
    assert isinstance(cte.args["alias"], exp.TableAlias), "cte_name reads the wrong node"
    assert isinstance(cte.args["alias"].this, exp.Identifier), (
        "the CTE name is no longer an Identifier under .this")


def test_a_cte_column_list_lives_under_the_alias():
    """`WITH c(a, b)` declines as unsupported, found through this key. If it
    moved, the column list would be dropped instead of declined — which changes
    what the query means."""
    cte = parse("WITH c(a, b) AS (SELECT 1, 2) SELECT 1").find(exp.CTE)
    assert cte.args["alias"].args.get("columns"), (
        "a CTE column list moved: it would now be dropped rather than declined")


# ---- comments --------------------------------------------------------------

def test_the_ast_comparison_is_blind_to_comments():
    """The reason `comments_equal` exists, stated correctly this time.

    Comments DO live in the tree -- sqlglot hangs them off whichever node is
    nearest (`parse("SELECT a -- note")` carries it on the Column, a leading
    block comment sits on the root) -- but `ast_equal` compares normalized
    reprs with `comments = None` on every node, so their presence changes
    nothing. An earlier version of this sentinel asserted comments were NOT in
    the tree, which one hand-picked input made true at the root and every other
    input falsified: a guard that can never fire, protecting a premise that was
    wrong.

    If either assertion below fails, `ast_equal` has started seeing comments
    and `comments_equal`/`CommentLoss` should be re-examined for redundancy.
    """
    from sqlalign.formatter import ast_equal

    carried = [node for node in parse("SELECT a -- note").walk() if node.comments]
    assert carried, "comments no longer attach to tree nodes at all"
    assert ast_equal("SELECT a -- note", "SELECT a", DIALECT), (
        "ast_equal now sees comments: the separate comment check may be redundant")
    assert ast_equal("SELECT a -- note", "SELECT a -- other", DIALECT)


# ---- the shapes the pin comment names --------------------------------------

def test_a_cast_carries_its_target_type_as_a_datatype():
    """`::int` and `CAST(x AS int)` collapse to one node, so which spelling is
    printed is sqlalign's decision and is read from here."""
    cast = parse("SELECT a::int FROM t").selects[0]
    assert isinstance(cast, exp.Cast), "`::` no longer parses to Cast"
    assert isinstance(cast.args["to"], exp.DataType), (
        "the cast target type moved off args['to']")


def test_a_time_unit_keeps_its_own_casing():
    """`DATE_TRUNC('month', d)` keeps `month` as a Var. Keyword casing must not
    reach it: upper-casing a unit inside a string literal would change the
    argument, not its presentation."""
    node = parse("SELECT DATE_TRUNC('month', d) FROM t").selects[0]
    unit = node.args.get("unit")
    assert unit is not None, "the unit moved off this node"
    assert isinstance(unit, exp.Var), (
        "the time unit is no longer a Var: keyword casing may now reach it")


def test_a_dollar_quoted_body_parses_as_a_heredoc():
    """sqlglot sees a `$$ ... $$` body as one opaque node, which is why
    `_plpgsql_ast_equal` compares those statements structurally rather than as a
    single tree, and why the body has to be split and formatted separately."""
    created = parse("CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql")
    assert isinstance(created.args.get("expression"), exp.Heredoc), (
        "a $$ body no longer parses as Heredoc: body splitting reads this")


# ---- dialect-specific shapes ----------------------------------------------

def test_only_postgres_distinguishes_the_two_negated_is_spellings():
    """`NOT x IS NULL` and `x IS NOT NULL` are one tree in T-SQL and Redshift and
    two in Postgres. `_keeps_negated_is_apart` probes this rather than listing
    dialects; if the answer changes, that probe silently starts rewriting SQL —
    or silently stops preserving it."""
    def shape(sql, dialect):
        return type(parse(sql, dialect).args["where"].this).__name__

    assert shape("SELECT 1 FROM t WHERE x IS NOT NULL", "postgres") == "Is"
    assert shape("SELECT 1 FROM t WHERE NOT x IS NULL", "postgres") == "Not"
    for dialect in ("tsql", "redshift"):
        assert shape("SELECT 1 FROM t WHERE x IS NOT NULL", dialect) == "Not"
        assert shape("SELECT 1 FROM t WHERE NOT x IS NULL", dialect) == "Not"


def test_a_pivot_hangs_off_the_table():
    """PIVOT is not a node the layout rebuilds — it rides on the table, which is
    why `table_name` renders it and why the Postgres decline is detected by
    looking at what comes out rather than from a hardcoded dialect list."""
    table = parse("SELECT * FROM t PIVOT (SUM(x) FOR y IN ('a'))", "tsql").find(exp.Table)
    assert table.args.get("pivots"), "PIVOT moved off the table node"


def test_tsql_offset_fetch_lands_in_the_limit_argument():
    """A `Fetch` in the `limit` slot, not a `Limit`. Taking one for the other
    raised an AttributeError a line above the decline it was meant to reach."""
    query = parse("SELECT a FROM t ORDER BY a OFFSET 5 ROWS FETCH NEXT 10 ROWS ONLY",
                  "tsql")
    assert isinstance(query.args.get("limit"), exp.Fetch), (
        "T-SQL OFFSET/FETCH moved out of the limit argument")


# ---- structural keys the layout walks --------------------------------------

def test_joins_are_a_list_on_the_select():
    """The FROM block is built by walking this list in order; a join that moved
    elsewhere would vanish from the output rather than fail loudly."""
    query = parse("SELECT a FROM t JOIN u ON t.i = u.i JOIN v ON v.i = t.i")
    joins = query.args["joins"]
    assert len(joins) == 2, "joins are no longer a flat list on the select"
    assert all(isinstance(join, exp.Join) for join in joins), "not all Join nodes"
    assert isinstance(joins[0].args["on"], exp.EQ), "the join condition moved off args['on']"


def test_the_version_under_test_is_recorded():
    """Not an assertion so much as a label: when one of the above fails, the
    first question is which sqlglot produced it."""
    assert sqlglot.__version__, "sqlglot has no version"
