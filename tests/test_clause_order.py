"""Clause ORDER, which `ast_equal` cannot check.

The safety net re-parses sqlalign's output and compares trees. That catches a
changed token — but not a valid-looking rearrangement, because **sqlglot reads
its own lenient output back without complaint**. Emit the clauses of a statement
in the wrong order and the guard shrugs.

Three bugs have now lived in that blind spot:

  - the legacy comma join, rebuilt as `FROM a JOIN b` (Postgres rejects a bare
    JOIN with no condition)
  - `FROM (VALUES (1, 2)) AS v(a, b)` beside a JOIN, rendered without its parens
  - T-SQL `OUTPUT`, emitted last. It shares the `returning` arg with Postgres's
    RETURNING but not its position: Postgres closes the statement with
    RETURNING, T-SQL puts OUTPUT BEFORE the body. `INSERT INTO t (a) SELECT …
    OUTPUT inserted.a` is not SQL Server syntax, and nothing said so.

This is the check that would have caught all three: sqlglot's own generator is
the authority on where a clause goes, so sqlalign's output must present the same
clause keywords in the same sequence. It compares ORDER only — never spelling,
spacing or line breaks, which are exactly what sqlalign is for.
"""
import re

import pytest
import sqlglot

from sqlalign.formatter import format_sql

# Clause-introducing keywords. Deliberately not every keyword in SQL: the point
# is the sequence of CLAUSES, and a word that can also appear inside an
# expression (`AS`, `AND`, `IN`) would make this a spelling test instead.
CLAUSE = re.compile(
    r"\b(?:INSERT INTO|DELETE FROM|GROUP BY|ORDER BY|ON CONFLICT|UNION ALL|"
    r"SELECT|UPDATE|MERGE INTO|FROM|WHERE|HAVING|LIMIT|OFFSET|FETCH|RETURNING|"
    r"OUTPUT|VALUES|SET|USING|JOIN|UNION|EXCEPT|INTERSECT|WINDOW|QUALIFY|FOR)\b")


def _clauses(sql: str) -> list[str]:
    return CLAUSE.findall(" ".join(sql.upper().split()))


CASES = [
    # T-SQL OUTPUT precedes the body; Postgres RETURNING closes the statement.
    ("tsql", "insert into t (a) output inserted.a select a from src;"),
    ("tsql", "update t set a = 1 output inserted.a where b = 2;"),
    ("tsql", "update t set a = 1 output inserted.a from u where b = 2;"),
    ("postgres", "insert into t (a) select b from u returning id;"),
    ("postgres", "update t set a = 1 from u where b = 2 returning a;"),
    ("postgres", "delete from t using u where a = 1 returning *;"),
    # The two older residents of this blind spot.
    ("postgres", "select 1 from a x, b y where x.id = y.id;"),
    ("postgres", "select * from (values (1, 2)) as v(a, b) join t on t.i = v.a;"),
    # Ordinary shapes, so a reordering anywhere shows up here too.
    ("postgres", "select a from t where b = 1 group by a having count(*) > 1 "
                 "order by a limit 5 offset 2;"),
    ("postgres", "select a from t for update skip locked;"),
    ("postgres", "select a from t1 union all select b from t2;"),
    ("postgres", "insert into t (a) values (1) on conflict (a) do nothing returning id;"),
    ("postgres", "merge into t using u on t.i = u.i when matched then delete;"),
    ("redshift", "select a from t qualify row_number() over (order by a) = 1;"),
    ("tsql", "select top 10 a from t order by a;"),
]


@pytest.mark.parametrize("dialect,sql", CASES)
def test_clause_order_matches_sqlglots_own(dialect, sql):
    """sqlglot's generator decides where a clause belongs; sqlalign only decides
    how it is laid out."""
    ours = format_sql(sql, dialect)
    assert ours.warnings == [], f"declined: {ours.warnings}"
    theirs = sqlglot.parse_one(sql, read=dialect).sql(dialect)
    assert _clauses(ours.text) == _clauses(theirs), (
        f"\n  sqlalign: {_clauses(ours.text)}\n  sqlglot : {_clauses(theirs)}")


def test_the_check_can_actually_fail():
    """A guard that cannot fire is decoration. This is the exact shape the T-SQL
    OUTPUT bug produced — valid-looking, wrongly ordered, and `ast_equal`-clean.
    """
    wrong = "INSERT INTO t (a) SELECT a FROM src OUTPUT inserted.a"
    right = "INSERT INTO t (a) OUTPUT inserted.a SELECT a FROM src"
    assert _clauses(wrong) != _clauses(right)

    from sqlalign.formatter import ast_equal
    assert ast_equal(right, wrong, "tsql"), (
        "ast_equal now catches this on its own; this file's premise has changed")


def test_it_does_not_object_to_layout():
    """Line breaks, leading commas and alignment padding are the whole product,
    so the check must be blind to them."""
    sql = "select a, b from t where c = 1 order by a;"
    out = format_sql(sql, "postgres").text
    assert "\n" in out and _clauses(out) == _clauses(
        sqlglot.parse_one(sql, read="postgres").sql("postgres"))
