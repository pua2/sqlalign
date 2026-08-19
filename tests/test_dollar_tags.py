"""Tagged dollar quotes: `$func$` rather than bare `$$`.

Postgres lets a dollar-quoted region carry a tag, and the tag is what makes
nesting possible -- `$outer$ ... $$inner$$ ... $outer$` is one region, not two.
A tag follows the rules for an unquoted identifier and is matched CASE-
SENSITIVELY: `$BODY$` is not closed by `$body$`.

Three defects lived here, all found by auditing the tag path and none of them
where the tag machinery itself was:

  * the tag regex was ASCII-only. `$café$` and `$ñ$` are legal Postgres, and a
    tag sqlalign does not recognise is a region nobody treats as opaque -- the
    file was cut at a `;` INSIDE the literal and the fragments formatted as SQL,
    so `<>` inside a string became `!=`. Stored data, changed, while the
    warnings said "passthrough";
  * `keyword_case = "lower"` lowered the tag itself, silently, at both ends. The
    result is valid SQL, which is why nothing objected -- but it is not what the
    author wrote, and the `dbt` preset sets that case;
  * comments inside a body were invisible to `comments_equal`, because the body
    is one token and `comment_text` did not descend into it. It returned `[]`
    for an entire procedure, so the comment guard was vacuously true for
    anything in one.
"""
import pytest

from sqlalign.formatter import comment_text, format_sql
from sqlalign.splitter import DOLLAR_TAG
from sqlalign.style import Style, preset_style

TAGS = ["$$", "$func$", "$body$", "$BODY$", "$MyTag$", "$_$", "$_x1$", "$sql$",
        "$select$", "$end$"]


def _fn(tag: str) -> str:
    return (f"create function f() returns int as {tag}\nbegin\n  return 1;\nend;\n"
            f"{tag} language plpgsql;")


@pytest.mark.parametrize("dialect", ["postgres", "redshift"])
@pytest.mark.parametrize("tag", TAGS)
def test_a_tagged_body_formats_and_keeps_its_tag(tag, dialect):
    result = format_sql(_fn(tag), dialect)
    assert not result.declines, [d.reason for d in result.declines]
    assert result.text.count(tag) == 2, f"tag not byte-identical at both ends: {result.text}"
    assert "RETURN 1;" in result.text, "the body was echoed, not laid out"
    assert format_sql(result.text, dialect).text == result.text, "not idempotent"


def test_a_nested_dollar_quote_is_not_the_end_of_the_body():
    """The reason tags exist. Splitting at the inner `$$` would truncate."""
    source = ("create function f() returns text as $outer$\nbegin\n"
              "  return $$inner text$$;\nend;\n$outer$ language plpgsql;")
    result = format_sql(source, "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert result.text.count("$outer$") == 2
    assert "$$inner text$$" in result.text


def test_mismatched_tag_case_is_not_quietly_paired():
    """`$BODY$ ... $body$` is an unterminated quote in Postgres. Treating the two
    as a matching pair would format something the engine would reject."""
    source = ("create function f() returns int as $BODY$ begin return 1; end; "
              "$body$ language plpgsql;")
    result = format_sql(source, "postgres")
    assert result.declines, "a mismatched pair was treated as closed"
    assert result.text == source


@pytest.mark.parametrize("sql", [
    "create function f(int) returns int as $$ select $1 + 1 $$ language sql;",
    "create function f(int,int) returns int as $fn$ select $1 + $2 $fn$ language sql;",
])
def test_a_positional_parameter_never_opens_a_quote(sql):
    """`$1` looks like a tag opener. A digit cannot lead a tag, which is what
    keeps the two apart."""
    result = format_sql(sql, "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert "$1" in result.text


# ---- the tag regex --------------------------------------------------------

@pytest.mark.parametrize(("text", "expected"), [
    ("$$", "$$"),
    ("$tag$", "$tag$"),
    ("$BODY$", "$BODY$"),
    ("$_x1$", "$_x1$"),
    ("$café$", "$café$"),        # a tag follows unquoted-identifier rules, not ASCII
    ("$ñ$", "$ñ$"),
    ("$1$", None),               # a digit cannot lead: this is not a tag
    ("$1", None),                # positional parameter
    ("$a b$", None),
])
def test_the_tag_pattern_follows_postgres(text, expected):
    match = DOLLAR_TAG.match(text)
    assert (match.group(0) if match else None) == expected


def test_a_non_ascii_tag_makes_the_region_opaque():
    """The defect this closes changed data. An unrecognised tag left the region
    open, so the file was cut at the `;` inside it and `<>` became `!=` inside a
    string literal -- with the warnings still saying "passthrough"."""
    source = "INSERT INTO docs (body) VALUES ($ñ$x;\nSELECT a <> b FROM t;\n$ñ$);\n"
    result = format_sql(source, "postgres")
    assert result.text == source, "content inside a dollar-quoted literal was rewritten"
    ascii_control = source.replace("$ñ$", "$q$")
    assert format_sql(ascii_control, "postgres").text == ascii_control
    assert "!=" not in result.text


def test_the_tag_pattern_has_one_definition():
    """It had two, in different modules under different names, and only one got
    widened when the first was found."""
    from sqlalign.layout.comments import _DOLLAR

    assert _DOLLAR is DOLLAR_TAG


# ---- keyword_case must not touch a delimiter ------------------------------

@pytest.mark.parametrize("tag", ["$BODY$", "$MyTag$", "$Func$"])
def test_keyword_case_does_not_lower_the_tag(tag):
    """The tag is a case-sensitive delimiter, not a keyword. Lowering it is
    valid SQL, which is why nothing objected -- and is not what was written."""
    result = format_sql(_fn(tag), "postgres", Style(keyword_case="lower"))
    assert not result.declines, [d.reason for d in result.declines]
    assert result.text.count(tag) == 2, result.text


def test_the_dbt_preset_does_not_lower_the_tag():
    """`dbt` sets keyword_case=lower, so it shipped this to everyone using it."""
    assert preset_style("dbt").keyword_case == "lower", "the preset changed; retarget this"
    result = format_sql(_fn("$BODY$"), "postgres", preset_style("dbt"))
    assert result.text.count("$BODY$") == 2, result.text


def test_the_body_itself_is_still_cased():
    """Only the delimiter is skipped. Skipping the whole region would leave a
    body's SQL uncased under `lower`."""
    result = format_sql(_fn("$b$"), "postgres", Style(keyword_case="lower"))
    assert "create function" in result.text, result.text


# ---- comments inside a body are under the guard ---------------------------

def test_comment_text_descends_into_a_body():
    """It returned `[]` for an entire procedure, so `comments_equal` could not
    fail for anything inside one."""
    source = ("create function f() returns int as $func$\ndeclare n int; -- keep me\n"
              "begin\n  return n;\nend;\n$func$ language plpgsql;")
    assert comment_text(source, "postgres") == ["keep me"]


def test_a_body_with_no_comment_is_not_tokenized_twice():
    """The descent is guarded by a substring scan: descending into every body
    doubled the tokenizing work for every procedure in a tree."""
    from sqlalign.formatter import _maybe_comment

    assert not _maybe_comment("begin return 1; end;")
    assert _maybe_comment("begin return 1; -- why\nend;")
    assert _maybe_comment("begin /* why */ return 1; end;")


@pytest.mark.parametrize(("body", "language", "expected"), [
    pytest.param("select 1 -- why", "sql", "SELECT 1; -- why", id="language-sql"),
    pytest.param("begin\n  insert into t select 1; -- why\n  return 1;\nend;",
                 "plpgsql", "INSERT INTO t SELECT 1; -- why", id="plpgsql-sql-clause"),
])
def test_the_terminator_never_lands_inside_a_trailing_comment(body, language, expected):
    """`_render_sql_stmt` appended `;` to the raw clause, so a trailing comment
    swallowed it: `select 1 -- why` shipped as `SELECT 1 -- why;`, leaving the
    statement unterminated. The same defect as the DECLARE one below, on the
    other rendering path, and nothing caught either until `comment_text` learned
    to look inside a body."""
    source = f"create function f() returns int as $s$\n{body}\n$s$ language {language};"
    result = format_sql(source, "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert expected in result.text, result.text


def test_an_own_line_comment_in_a_language_sql_body_survives():
    source = "create function f() returns int as $s$\n-- why\nselect 1\n$s$ language sql;"
    result = format_sql(source, "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert "-- why\nSELECT 1;" in result.text, result.text


@pytest.mark.parametrize("tag", ["$$", "$func$"])
def test_a_trailing_comment_on_a_declare_survives(tag):
    """`_render_declare` handed the whole declaration to `_upper_kw`, which
    whitespace-tokenizes whatever it is given -- so the comment was keyword-cased
    and the terminator landed inside it:

        DECLARE n int; -- count FROM the source TABLE;
    """
    source = (f"create or replace function f() returns int as {tag}\n"
              f"declare n int; -- count from the source table\nbegin\n  return n;\nend;\n"
              f"{tag} language plpgsql;")
    result = format_sql(source, "postgres")
    assert not result.declines, [d.reason for d in result.declines]
    assert "-- count from the source table" in result.text, result.text
    assert "DECLARE n INT;" in result.text, "the declaration itself stopped being cased"
    assert "TABLE;" not in result.text, "the terminator is inside the comment"
