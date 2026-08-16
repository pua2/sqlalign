"""dollar-quoted plpgsql bodies and the dollar-quote-aware
safety equality."""
import pytest
from conftest import load_pair

from sqlalign.formatter import ast_equal, format_sql
from sqlalign.plpgsql import split_body

# The two goldens with a dollar-quoted body: 19 is a FUNCTION, 20 a PROCEDURE.
BODY_SAMPLES = ["19", "20"]

# ---- split_body (dollar-quote scanner) ------------------------------------

def test_split_body_roundtrip():
    stmt = "CREATE FUNCTION f() RETURNS INT\nLANGUAGE plpgsql\nAS $$\nBEGIN\nRETURN 1;\nEND;\n$$;"
    header, body, tail = split_body(stmt)
    assert header + body + tail == stmt
    assert body.strip().startswith("BEGIN")
    assert tail.startswith("$$")


def test_non_dollar_statement_returns_none():
    assert split_body("SELECT 1;") is None


def test_split_body_named_tag():
    stmt = "CREATE FUNCTION f() RETURNS INT LANGUAGE plpgsql AS $body$ BEGIN RETURN 1; END; $body$;"
    header, body, tail = split_body(stmt)
    assert header.endswith("$body$")
    assert tail == "$body$;"
    assert header + body + tail == stmt


# ---- goldens format byte-exact and are idempotent -------------------------

@pytest.mark.parametrize("sid", BODY_SAMPLES)
def test_golden_body_is_byte_exact(sid):
    inp, expected = load_pair(sid)
    assert format_sql(inp, "postgres").text == expected


@pytest.mark.parametrize("sid", BODY_SAMPLES)
def test_bodies_are_idempotent(sid):
    expected = load_pair(sid)[1]
    assert format_sql(expected, "postgres").text == expected


@pytest.mark.parametrize("sid", BODY_SAMPLES)
def test_no_warnings_on_formatted_bodies(sid):
    assert format_sql(load_pair(sid)[0], "postgres").warnings == []


# ---- specific layout details ----------------------------------------------

def test_select_into_layout():
    """`SELECT … INTO var` rides on the last select-item line."""
    body = "select sum(total) into v_ltv from orders where id = 1;"
    assert format_sql(body, "postgres").text.splitlines()[0] == "SELECT SUM(total) INTO v_ltv"


def test_multi_target_select_into_declines():
    """`SELECT a, b INTO x, y` (multiple targets) is not modeled — it declines
    to byte-identical passthrough rather than dropping the extra targets."""
    stmt = "select a, b into x, y from t;"
    result = format_sql(stmt, "postgres")
    assert result.text.strip() == stmt
    assert result.warnings


def test_get_diagnostics_casing():
    """The assignment target stays a variable; the diagnostic item uppercases,
    even when they are the same word."""
    out = format_sql(load_pair("20")[0], "postgres").text
    assert "GET DIAGNOSTICS row_count = ROW_COUNT;" in out


def test_if_then_else_geometry():
    out = format_sql(load_pair("20")[0], "postgres").text
    assert "IF row_count = 0\n  THEN RAISE WARNING" in out
    assert "\n  ELSE RAISE NOTICE" in out
    assert "\nEND IF;" in out


def test_quoted_language_input_still_formats():
    """A real-world `LANGUAGE 'plpgsql'` (quoted, legacy) input must format to
    the bare canonical form — the dollar-aware safety check accepts it."""
    inp = ("create function g() returns int language 'plpgsql' as $$ begin "
           "return 1; end; $$;")
    result = format_sql(inp, "postgres")
    assert result.warnings == []
    assert "LANGUAGE plpgsql\nAS $$" in result.text


# ---- dollar-quote-aware safety equality -------------------------

def test_ast_equal_accepts_reformatted_body():
    inp, expected = load_pair("19")
    assert ast_equal(inp, expected, "postgres")


def test_ast_equal_rejects_changed_body_sql():
    """A body whose SQL statement was semantically altered is NOT equal — the
    per-statement AST comparison catches it (fail-safe → passthrough)."""
    good = ("create function f() returns int language plpgsql as $$ begin "
            "select a from t where x = 1; return 1; end; $$;")
    bad = ("create function f() returns int language plpgsql as $$ begin "
           "select a from t where x = 2; return 1; end; $$;")
    assert not ast_equal(good, bad, "postgres")


def test_ast_equal_rejects_changed_header():
    good = "create function f() returns int language plpgsql as $$ begin return 1; end; $$;"
    bad = "create function f() returns text language plpgsql as $$ begin return 1; end; $$;"
    assert not ast_equal(good, bad, "postgres")


def test_ast_equal_rejects_dropped_statement():
    good = ("create function f() returns int language plpgsql as $$ begin "
            "perform log_it(); return 1; end; $$;")
    bad = ("create function f() returns int language plpgsql as $$ begin "
           "return 1; end; $$;")
    assert not ast_equal(good, bad, "postgres")


# ---- unmodeled constructs degrade to passthrough ----------------

def test_unmodeled_body_passes_through():
    """A body with a construct v1 does not model (a FOR loop) passes through
    byte-identical with a warning, never mangled."""
    inp = ("create function f() returns int language plpgsql as $$ begin "
           "for r in select 1 loop return r; end loop; end; $$;")
    result = format_sql(inp, "postgres")
    assert result.text == inp
    assert result.warnings  # a warning was emitted
