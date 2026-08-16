"""Unverified dialects are refused, not mis-formatted.

Found while scoping T-SQL support. `format_sql(sql, "tsql")` used to
"work": it produced output, emitted no warning, and PASSED the AST safety check —
while silently turning `SELECT TOP 10 id FROM users` into
`SELECT id FROM users LIMIT 10`, which SQL Server rejects outright.

The chain:
  1. sqlglot parses T-SQL `TOP 10` into the same `Limit` node Postgres uses.
  2. The layout emits a hardcoded `LIMIT` keyword — handlers are dialect-agnostic,
     which is TRUE for postgres/redshift (they share this syntax) and FALSE for T-SQL.
  3. sqlglot then parses that `LIMIT` back as T-SQL too, so `ast_equal` compares
     equal and nothing is flagged.

The lesson is about the guarantee itself: **the AST safety net cannot detect
dialect-invalid output**, because sqlglot accepts far more than any single engine.
"sqlalign cannot change what your SQL means" holds WITHIN a verified dialect;
extending it to a new one requires auditing every keyword the handlers emit
(2 of 26 diverge for T-SQL: LIMIT and OFFSET), not just registering a parser.

Refusing is therefore the only correct behaviour until that audit is done.
"""
import argparse
from unittest.mock import patch

import pytest
import sqlglot

from sqlalign.cli import main
from sqlalign.formatter import format_sql
from sqlalign.style import SUPPORTED_DIALECTS


@pytest.mark.parametrize("dialect", sorted(SUPPORTED_DIALECTS))
def test_supported_dialects_work(dialect):
    assert format_sql("select a from t;", dialect).text.startswith("SELECT a")


@pytest.mark.parametrize("dialect", ["mysql", "bigquery", "snowflake", "oracle", "duckdb"])
def test_unverified_dialects_are_refused(dialect):
    with pytest.raises(ValueError) as e:
        format_sql("select a from t;", dialect)
    assert dialect in str(e.value)
    assert "postgres" in str(e.value)          # the error names what IS supported


def test_the_tsql_corruption_cannot_recur():
    """The original regression: `SELECT TOP 10` silently became `... LIMIT 10`,
    which SQL Server rejects, with no warning and ast_equal returning True.
    T-SQL is now supported, so the assertion is that TOP SURVIVES and LIMIT is
    never emitted -- the corruption itself, not the refusal."""
    sql = "SELECT TOP 10 id, name FROM users ORDER BY id;"
    result = format_sql(sql, "tsql")
    assert "LIMIT" not in result.text.upper()
    assert result.text.startswith("SELECT TOP 10 id")
    assert result.warnings == []


def test_refusal_is_loud_not_a_passthrough():
    """A passthrough would be the wrong response for an unsupported DIALECT: that
    is a caller error, not an unmodelled statement, so it must fail where it can
    be seen rather than silently mid-file."""
    with pytest.raises(ValueError):
        format_sql("select a from t;", "mysql")


def test_cli_offers_only_verified_dialects():
    """The CLI's --dialect choices must not drift ahead of what is verified."""
    seen = {}
    real = argparse.ArgumentParser.add_argument

    def capture(self, *args, **kwargs):
        if args and args[0] == "--dialect":
            seen["choices"] = set(kwargs.get("choices", []))
        return real(self, *args, **kwargs)

    with patch.object(argparse.ArgumentParser, "add_argument", capture), \
         pytest.raises(SystemExit):
        main(["--help"])
    assert seen["choices"] == set(SUPPORTED_DIALECTS)


def test_redshift_text_rewrite_is_caught_not_shipped():
    """sqlglot renders redshift TEXT as VARCHAR(MAX), which changes the width
    (TEXT = VARCHAR(256), VARCHAR(MAX) = VARCHAR(65535)). Unlike the T-SQL type
    collapses, these parse to DIFFERENT nodes, so ast_equal rejects the rewrite
    and the statement passes through byte-identical. Pinned because the failure
    mode if this ever stopped being caught is silent data-definition corruption."""
    for sql in ("create table t (a text not null, b int);",
                "select cast(x as text) from t;"):
        result = format_sql(sql, "redshift")
        assert result.text == sql, "rewrite escaped the safety net"
        # Reported as an UPSTREAM decline rather than a semantic one: the
        # rewrite is sqlglot's, so sqlglot cannot round-trip the statement
        # through its own generator either and no formatter built on it could.
        # The distinction is load-bearing -- "would change semantics" is the
        # wording that means sqlalign's renderer is at fault, and the suite
        # sweeps for it (tests/test_no_silent_declines.py).
        assert result.declines[0].kind == "upstream"
        assert any("cannot round-trip" in w for w in result.warnings)


def test_postgres_float_types_are_not_collapsed():
    """Postgres REAL/FLOAT/DOUBLE PRECISION must keep their distinct meanings --
    the T-SQL parse-time collapse (REAL and FLOAT to one node) does not apply
    here, and a regression would silently widen column precision."""
    parsed = {src: sqlglot.parse_one(f"CAST(x AS {src})", read="postgres").args["to"].this
              for src in ("REAL", "DOUBLE PRECISION")}
    assert parsed["REAL"] != parsed["DOUBLE PRECISION"]
    assert format_sql("select cast(x as real) from t;", "postgres").warnings == []
