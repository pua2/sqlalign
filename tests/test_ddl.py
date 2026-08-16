"""DDL layout: cross-cutting checks beyond the golden fixtures."""
import sqlglot

from sqlalign.casing import render_expr
from sqlalign.formatter import format_sql


def test_render_expr_neq_is_bang_equals():
    # The house generator canonicalizes NEQ to `!=` everywhere render_expr runs
    # (not only in the laid-out condition path).
    node = sqlglot.parse_one("select a where x <> 1", read="postgres").args["where"].this
    assert render_expr(node, "postgres") == "x != 1"


def test_create_index_partial_where_neq():
    out = format_sql("create index i on t (a) where b != 1;\n").text
    assert out == "CREATE INDEX i ON t (a) WHERE b != 1;\n"
    assert format_sql(out).text == out


def test_grant_command_fallback_uppercases_keywords():
    # GRANT ... ON ALL TABLES IN SCHEMA parses only as an unsupported Command;
    # keywords still get uppercased, identifiers preserved.
    out = format_sql("grant select on all tables in schema analytics to readonly_user;\n").text
    assert out == "GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO readonly_user;\n"


def test_truncate_options_uppercase_second_line():
    out = format_sql("truncate table s restart identity cascade;\n").text
    assert out == "TRUNCATE TABLE s\nRESTART IDENTITY CASCADE;\n"


def test_create_schema_formats():
    """It used to be the example of a CREATE sqlalign does not model. There is
    nothing in it to model: no list, no column, nothing for the resolver to hold
    a column for -- so it is one line, spelled by sqlglot's generator."""
    assert format_sql("create schema analytics;\n").text == "CREATE SCHEMA analytics;\n"


def test_unsupported_create_passes_through():
    # a CREATE we don't model still degrades to safe passthrough. CREATE
    # EXTENSION carries a `properties` shape nothing here reads.
    src = "create extension if not exists postgis;\n"
    r = format_sql(src)
    assert r.text == src
    assert r.warnings
