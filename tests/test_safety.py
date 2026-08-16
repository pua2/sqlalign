from sqlalign.formatter import ast_equal, format_sql


def test_ast_equal_ignores_whitespace_and_comments():
    assert ast_equal("select a,b from t", "SELECT a, b\nFROM t -- hi", "postgres")


def test_ast_equal_detects_difference():
    assert not ast_equal("select a from t", "select b from t", "postgres")


def test_unparseable_statement_passes_through_with_warning():
    r = format_sql("FROBNICATE THE THINGS;\n")
    assert r.text == "FROBNICATE THE THINGS;\n"
    assert any("passthrough" in w for w in r.warnings)


def test_semantic_change_is_caught_and_passed_through(monkeypatch):
    # FIX 1 (defense-in-depth): a per-statement render that would change
    # semantics must NOT abort the file. format_sql catches the SafetyError
    # internally and passes the statement through byte-identical + warns.
    import sqlalign.formatter as fmt
    monkeypatch.setattr(fmt, "_format_statement", lambda s, d, w: "SELECT 999;")
    result = fmt.format_sql("SELECT 1;\n")   # must not raise
    assert result.text == "SELECT 1;\n"
    assert any("formatting would change semantics" in w for w in result.warnings)


def test_warning_line_number_correct_for_duplicate_statements():
    text = "SELECT 1;\n-- c2\n-- c3\nFROBNICATE THE THINGS;\nFROBNICATE THE THINGS;\n"
    r = format_sql(text)
    lines = list(r.warnings)
    assert len(lines) == 2
    assert "line 2" in lines[0]   # first chunk's content starts at its comment block, line 2
    assert "line 5" in lines[1]


def test_comment_only_tail_no_warning():
    r = format_sql("SELECT 1;\n-- trailing comment at EOF\n")
    assert r.warnings == []
    assert r.text == "SELECT 1;\n-- trailing comment at EOF\n"


def test_ast_equal_false_when_b_unparseable():
    assert ast_equal("SELECT 1", "FROBNICATE THE THINGS", "postgres") is False


def test_ast_equal_tolerates_keyword_case_in_command_and_var():
    assert ast_equal("truncate table t restart identity cascade",
                     "TRUNCATE TABLE t RESTART IDENTITY CASCADE", "postgres")
    assert ast_equal("grant select on t to r", "GRANT SELECT ON t TO r", "postgres")


def test_ast_equal_still_rejects_string_literal_case_change():
    assert not ast_equal("select 'Alpha'", "select 'ALPHA'", "postgres")


def test_ast_equal_rejects_quoted_identifier_case_in_command():
    a = 'GRANT SELECT ON ALL TABLES IN SCHEMA "Analytics" TO readonly_user;'
    b = 'GRANT SELECT ON ALL TABLES IN SCHEMA "analytics" TO readonly_user;'
    assert not ast_equal(a, b, "postgres")


def test_ast_equal_rejects_string_literal_case_in_command():
    a = "CREATE POLICY p ON t USING (role = 'Admin');"
    b = "CREATE POLICY p ON t USING (role = 'admin');"
    assert not ast_equal(a, b, "postgres")


def test_ast_equal_rejects_quoted_var_case():
    assert not ast_equal('SET search_path = "MySchema";',
                         'SET search_path = "myschema";', "postgres")


def test_ast_equal_still_tolerates_bare_keyword_case():
    # Regression guard for the four rejects-tests above: making quoted identifiers
    # and string literals case-SIGNIFICANT must not drag bare keywords along with
    # them. Note the mixed `ON t to r` -- unquoted keyword case stays inert.
    assert ast_equal("truncate table t restart identity cascade",
                     "TRUNCATE TABLE t RESTART IDENTITY CASCADE", "postgres")
    assert ast_equal("grant select on t to r",
                     "GRANT SELECT ON t to r", "postgres")


def test_tokenizer_error_statement_passes_through():
    r = format_sql("SELECT 'abc;\nSELECT 1;\n")
    assert r.text == "SELECT 'abc;\nSELECT 1;\n"
    assert len(r.warnings) >= 1


def test_ast_equal_false_on_tokenizer_error():
    assert ast_equal("SELECT 1", "SELECT 'abc", "postgres") is False
