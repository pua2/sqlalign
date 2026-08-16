"""No statement may pass through with `would change semantics` as its reason.

There are two kinds of decline and only one of them is the product.

A NAMED decline — `Pivot`, `CTE MATERIALIZED`, `Alter` — is a construct sqlalign
has decided not to model. It is deliberate, it is reported by `--report` under
its own cause, and it is the documented contract.

A decline whose reason is "formatting would change semantics" is a BUG. It means
the renderer emitted something that means a different thing, and the re-parse
guard caught it. The statement is safe, which is exactly why nobody notices: it
looks identical to a construct that was declined on purpose.

Two shipped that way and both were found by accident:

  - every lowercase call to a user-defined function, because sqlglot parses an
    unknown function as `exp.Anonymous` whose `this` is a case-preserved string,
    and casing it made `ast_equal` see a change
  - every quoted column alias, because `Alias.alias` returns the identifier's
    NAME with the quoting stripped, so `AS "Total Revenue"` came out as a
    syntax error

This sweep is the guard neither of them had. It is not a search for unsupported
constructs — it asserts that whatever sqlalign DOES format, it formats without
changing what the statement means.
"""
import itertools

import pytest

from sqlalign.formatter import format_sql

SILENT = "would change semantics"

# Identifier spellings, which is where both known instances of this bug lived:
# quoting, case, reserved words, and characters that only survive quoted.
IDENTS = ['a', '"a"', '"A"', '"MixedCase"', '"My Col"', '"select"', '"a b"',
          '"café"', '"日本語"', '"has ""quote"""', 'T.a', '"t"."a"']

SHAPES = [
    "select {i} from t;",
    "select {i} as x from t;",
    "select x as {i} from t;",
    "select {i} from t where {i} = 1;",
    "select {i} from t group by {i};",
    "select {i} from t order by {i};",
    "select count({i}) from t;",
    "select {i} from t join u on u.k = {i};",
    "select sum({i}) over (partition by {i}) from t;",
    "select case when {i} then 1 end from t;",
    "with c as (select {i} from t) select * from c;",
    "insert into t ({i}) values (1);",
    "update t set {i} = 1;",
    "select distinct {i} from t;",
    "select {i} from t limit 1;",
    "select {i} from t x;",
    "select {i} from (select {i} from u) d;",
]

# Function calls, the other known instance: anything sqlglot has no node for.
CALLS = ["my_udf(a)", "MY_UDF(a)", "my_schema.my_udf(a)", "compute_ltv(t.id)",
         "coalesce(my_udf(a), 0)", "sum(x)", "SUM(x)", "count(*)",
         "jsonb_array_elements(x)", "date_trunc('day', ts)"]

CALL_SHAPES = [
    "select {c} from t;",
    "select {c} as v from t;",
    'select {c} as "V" from t;',
    "select a from t where {c} = 1;",
    "select a from t group by {c};",
    "select a from t order by {c};",
]


# Predicates, added after the sweep MISSED one. `x NOT LIKE 'a'` is a single
# `exp.Like` carrying negation as a FLAG rather than a wrapping `Not`; the flag
# was unread, so the renderer emitted plain `LIKE` and the guard caught it. The
# identifier and function matrices above could never have produced it -- a sweep
# only covers the axes you give it, which is the whole lesson of this file.
PREDICATES = [
    "x = 1", "x != 1", "x > 1", "x < 1",
    "x LIKE 'a%'", "x NOT LIKE 'a%'", "x ILIKE 'a%'", "x NOT ILIKE 'a%'",
    "x LIKE 'a%' ESCAPE '!'", "x NOT LIKE 'a%' ESCAPE '!'",
    "x IN (1, 2)", "x NOT IN (1, 2)",
    "x BETWEEN 1 AND 2", "x NOT BETWEEN 1 AND 2",
    "x IS NULL", "x IS NOT NULL",
    "x IS DISTINCT FROM y", "x IS NOT DISTINCT FROM y",
    "x IN (SELECT y FROM u)", "x NOT IN (SELECT y FROM u)",
    "EXISTS (SELECT 1 FROM u)", "NOT EXISTS (SELECT 1 FROM u)",
]

PREDICATE_SHAPES = [
    "select a from t where {p};",
    "select a from t where {p} and b = 2;",
    "select a from t where b = 2 and {p};",
    "select a from t where ({p} or b = 2) and c = 3;",
    "select a from t group by a having {p};",
    "select a from t join u on u.k = t.k and {p};",
]


def _silent(sql, dialect="postgres"):
    result = format_sql(sql, dialect)
    return [w for w in result.warnings if SILENT in w]


@pytest.mark.parametrize("shape", SHAPES)
def test_no_identifier_spelling_declines_silently(shape):
    offenders = [sql for i in IDENTS if (sql := shape.format(i=i)) and _silent(sql)]
    assert not offenders, f"renderer changed meaning: {offenders}"


@pytest.mark.parametrize("shape", CALL_SHAPES)
def test_no_function_call_declines_silently(shape):
    offenders = [sql for c in CALLS if (sql := shape.format(c=c)) and _silent(sql)]
    assert not offenders, f"renderer changed meaning: {offenders}"


@pytest.mark.parametrize("shape", PREDICATE_SHAPES)
def test_no_predicate_declines_silently(shape):
    offenders = [sql for p in PREDICATES if (sql := shape.format(p=p)) and _silent(sql)]
    assert not offenders, f"renderer changed meaning: {offenders}"


@pytest.mark.parametrize("dialect", ["postgres", "redshift", "tsql"])
def test_the_sweep_holds_across_dialects(dialect):
    offenders = [sql for shape, i in itertools.product(SHAPES[:8], IDENTS)
                 if (sql := shape.format(i=i)) and _silent(sql, dialect)]
    assert not offenders, f"renderer changed meaning under {dialect}: {offenders}"


def test_the_sweep_can_actually_fail():
    """A guard that cannot fire is decoration. This is the exact statement that
    tripped it before `column_alias` existed — it must format now, and the
    detector must recognise the message it used to produce.
    """
    assert not _silent('select revenue as "Total Revenue" from t;')
    assert SILENT in (
        "formatting would change semantics, passed through unformatted: …"), (
        "the warning text moved; this sweep is now blind")


def test_a_named_decline_is_not_flagged():
    """The sweep must not confuse a deliberate decline with a bug."""
    result = format_sql("select * from t pivot (sum(x) for y in (1, 2)) p;", "postgres")
    assert result.warnings, "premise changed: PIVOT now formats"
    assert not _silent("select * from t pivot (sum(x) for y in (1, 2)) p;")
    assert result.declines[0].reason
