"""Casing a statement from its own source: what counts as a keyword.

The layout renders by handing nodes to sqlglot's generator, which is right for
almost everything and is what makes the columnar style possible. For a handful
of statements it is lossy -- two spellings collapse to one node, so the author's
is gone before the generator runs. Since 1.2 those were caught and passed
through: honest, but they did not format at all.

They are now cased from the source instead, which cannot lose a spelling because
it never rebuilds anything. The whole difficulty is telling a keyword from an
identifier, and neither obvious answer works:

  * the tokenizer types `add`, `to` and `local` as `VAR` in exactly these
    statements, so trusting it leaves them lowercase next to an uppercased
    `ALTER TABLE`;
  * the keyword table says `year`, `name` and `value` are keywords, so trusting
    it renames a column.

The parse tree separates them. What the author wrote as a name or a value is a
node; the grammar around it lives in the generator and is on no node at all.
"""
import pytest
import sqlglot

from sqlalign.formatter import format_sql
from sqlalign.sourcecase import content_words, recase, renders_from_source


def _cased(sql, dialect="postgres", keyword_case="upper"):
    return recase(sql, sqlglot.parse_one(sql, dialect=dialect), dialect, keyword_case)


@pytest.mark.parametrize(("sql", "expected"), [
    ("alter table t add column c integer array",
     "ALTER TABLE t ADD COLUMN c INTEGER ARRAY"),
    ("alter table t alter column x type text",
     "ALTER TABLE t ALTER COLUMN x TYPE TEXT"),
    ("drop function f()", "DROP FUNCTION f()"),
    ("set search_path to public", "SET search_path TO public"),
    ("set local work_mem = '64MB'", "SET LOCAL work_mem = '64MB'"),
])
def test_grammar_is_cased_and_content_is_not(sql, expected):
    assert _cased(sql) == expected


@pytest.mark.parametrize("sql", [
    "alter table t add column year int",
    "alter table t add column name text",
    "alter table t add column value numeric",
    "alter table t add column date timestamp",
])
def test_a_column_named_like_a_keyword_keeps_its_name(sql):
    """The keyword table would rename all four. The tree knows they are names."""
    name = sql.split("column ")[1].split()[0]
    assert f"COLUMN {name} " in _cased(sql), _cased(sql)


def test_a_quoted_identifier_is_never_touched():
    """Quoting makes case part of the name, so changing it changes which column
    is meant. Quoted identifiers are content by token type, before the tree is
    consulted at all."""
    assert _cased('alter table "Type" add column "Order" text') == (
        'ALTER TABLE "Type" ADD COLUMN "Order" TEXT')


def test_a_word_used_as_both_keeps_the_spelling_it_was_given():
    """`type` is grammar in `ALTER COLUMN ... TYPE` and a name as the table.
    There is one answer per word, and leaving it as written is the safe one."""
    cased = _cased('alter table type alter column x type text')
    assert " type " in cased, cased
    assert "TYPE" not in cased


def test_string_literals_and_spacing_come_through_byte_identical():
    sql = "set  local   work_mem   =   'Sixty Four MB'"
    cased = _cased(sql)
    assert "'Sixty Four MB'" in cased
    assert cased == "SET  LOCAL   work_mem   =   'Sixty Four MB'", "spacing changed"


def test_a_comment_survives_because_it_is_between_the_spans():
    """Only token spans are rewritten. Everything between them -- whitespace and
    comments alike -- is copied from the source."""
    cased = _cased("alter table t /* keep me */ add column c int")
    assert "/* keep me */" in cased


def test_keyword_case_is_honoured():
    """Type names are grammar too, so `INT` lowercases with the rest of them."""
    assert _cased("ALTER TABLE t ADD COLUMN c INT", keyword_case="lower") == (
        "alter table t add column c int")


def test_a_command_is_not_eligible():
    """No content nodes, so every word would read as grammar and a role name
    would be uppercased."""
    assert not renders_from_source(sqlglot.parse_one("SET ROLE reporting",
                                                     dialect="postgres"))
    assert renders_from_source(sqlglot.parse_one("SET search_path TO public",
                                                 dialect="postgres"))


def test_content_words_are_casefolded_names_and_values():
    words = content_words(sqlglot.parse_one("alter table Orders add column Year int",
                                            dialect="postgres"))
    assert {"orders", "year"} <= words
    assert "add" not in words and "column" not in words


def test_it_is_a_fixed_point():
    """Casing twice is casing once, which is what makes it safe as a formatter
    output rather than a one-off repair."""
    for sql in ["alter table t add column c integer array", "set search_path to public"]:
        once = format_sql(sql + ";", "postgres").text
        assert format_sql(once, "postgres").text == once
