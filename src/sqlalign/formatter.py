import re
from collections import namedtuple

import sqlglot
from sqlglot import exp

from sqlalign import keywordcase, plpgsql, templating
from sqlalign.align import apply_align_targets, render
from sqlalign.casing import parse_dialect, render_style
from sqlalign.commas import apply_comma_position
from sqlalign.layout import Unsupported, layout_statement
from sqlalign.layout import comments as _comments
from sqlalign.splitter import split_statements
from sqlalign.style import HOUSE, SUPPORTED_DIALECTS, Style

# `statements` is every top-level statement seen; `declines` is one Decline per
# statement passed through unformatted. Both carry defaults so the two-field
# construction used throughout still works.
Decline = namedtuple("Decline", "kind reason")
FormatResult = namedtuple("FormatResult", "text warnings statements declines",
                          defaults=(0, ()))

# `-- sqlalign: skip` passes the whole statement through byte-identical, with no
# parse and no warning. Recognised both in a statement's leading comment block
# and as a trailing same-line comment after the terminating ';'.
_SKIP_RE = re.compile(r"--\s*sqlalign:\s*skip", re.IGNORECASE)

# A trailing same-line comment after a statement's terminating ';': the ';', any
# whitespace, one `--`-to-EOL or `/* ... */` comment, then only whitespace to the
# end of the statement text. Anchoring to the ';' keeps a `--`/`;` that lives
# inside a string literal from being mistaken for a trailing comment. Used to
# peel the comment off so the body formats and re-attach it verbatim, and to
# recognise a trailing `-- sqlalign: skip`.
_TRAILING_COMMENT_RE = re.compile(r";\s*(?:--[^\n]*|/\*.*?\*/)\s*\Z", re.DOTALL)


class SafetyError(RuntimeError):
    pass


# Types whose distinction sqlglot destroys at parse time for T-SQL, where the two
# spellings are not synonyms:
#   REAL  is FLOAT(24), FLOAT is FLOAT(53) , rewriting one widens precision
#   NTEXT is Unicode,   TEXT  is not
# Both collapse to a single node before any generator runs, so `ast_equal` compares
# the two spellings EQUAL and cannot catch the change. The AST is useless here; the
# only defence is to spot the type in the RAW SOURCE and decline the statement.
# (INT/INTEGER, DECIMAL/NUMERIC and TIMESTAMP/ROWVERSION also collapse but are true
# synonyms, so their canonicalisation is harmless: the same call the house style
# already makes for DECIMAL->NUMERIC.)
_TSQL_LOSSY_TYPES = re.compile(r"\b(REAL|NTEXT)\b", re.IGNORECASE)

# A lone T-SQL batch separator, once the splitter has isolated it.
_GO_ONLY = re.compile(r"GO(?:\s+\d+)?", re.IGNORECASE)

# A GO line in context, used to split a file into batches for the safety check.
_GO_LINE_RE = re.compile(r"^[ \t]*GO(?:[ \t]+\d+)?[ \t]*$", re.IGNORECASE | re.MULTILINE)


def _split_go_batches(text: str) -> tuple[list[str], list[str]]:
    """`(batches, separators)` for a T-SQL file. Separators are normalised so a
    reformatted `go` still matches a source `GO`, while a MISSING one does not."""
    batches, separators, pos = [], [], 0
    for match in _GO_LINE_RE.finditer(text):
        batches.append(text[pos:match.start()])
        separators.append(match.group(0).strip().upper())
        pos = match.end()
    batches.append(text[pos:])
    return batches, separators


# Node/arg shapes (verified empirically against pinned sqlglot v30.14) that hold
# raw, case-preserved *keyword* text rather than user data, and are therefore safe
# to casefold before AST-equality comparison. Keyword casing is semantically inert
# in SQL; string/identifier casing is not, so those are deliberately excluded below.
#   - exp.Command: unsupported-syntax fallback (e.g. `GRANT ... ON ALL TABLES IN
#     SCHEMA ...` in postgres). Both `this` (the command keyword, e.g. "grant") and
#     `expression` (the entire remainder of the statement, verbatim) are plain str
#     holding the *raw, half-parsed source text*, which can itself embed quoted
#     identifiers (`"Analytics"`) or string literals (`'Admin'`) whose casing IS
#     semantically significant. Casefolding this wholesale would silently mask
#     real case changes inside those quoted regions, so it is handled separately
#     below via `_casefold_outside_quotes`, not through this str-arg table.
#   - exp.Var: generic keyword-value wrapper. Deliberately not casefolded here as
#     a blanket rule (see `_CASEFOLD_VAR_ARGS` below): a bare Var can also hold
#     semantically significant, case-preserved user data. For example
#     `SET search_path = "MySchema"` parses to `Var(this="MySchema")` with the
#     quotes stripped by the parser; folding all Var.this globally would make
#     that quoted-identifier case change invisible to ast_equal.
#   - exp.WindowSpec: frame clause, e.g. `ROWS BETWEEN UNBOUNDED PRECEDING AND
#     CURRENT ROW`. `kind` ("ROWS"/"RANGE") and `start_side`/`end_side`
#     ("PRECEDING"/"FOLLOWING") are plain str with source-preserved casing; `start`/
#     `end` are hardcoded-casing str (e.g. always "UNBOUNDED") or Literal nodes, and
#     `exclude` is an exp.Var (already covered), so none of those three need entries.
#   - exp.TruncateTable: `identity` (e.g. "RESTART") and `option` (e.g. "CASCADE")
#     are plain str with source-preserved casing.
#   - exp.Anonymous: a function sqlglot has no node for, i.e. any
#     user-defined function and any builtin outside its dialect table. Its `this` is the
#     function NAME as a case-preserved plain str. sqlalign cases function names
#     like any other keyword, so `my_udf(x)` renders as `MY_UDF(x)` and the two
#     strings differ. Unquoted SQL function names are case-insensitive (Postgres
#     folds them), so that is not a semantic change, but without this entry
#     ast_equal saw one and passed the statement through. Every lowercase call to
#     unknown function would otherwise be declined.
#   - exp.Comment: `kind` is the commented object's type ("TABLE" / "COLUMN") as
#     a case-preserved plain str, which sqlglot echoes back verbatim: so
#     `comment on table t` renders `COMMENT ON table t`, one lowercase keyword
#     stranded in an uppercased statement. Casing it is right; without this entry
#     the cased output compares unequal and the statement declines instead.
_CASEFOLD_STR_ARGS = {
    exp.WindowSpec: ("kind", "start_side", "end_side"),
    exp.TruncateTable: ("identity", "option"),
    exp.Anonymous: ("this",),
    exp.Comment: ("kind",),
}

# Narrow, node-scoped exception to the "no bare Var casefold" rule above.
# Empirically (pinned sqlglot v30.14), redshift `DISTSTYLE {KEY|EVEN|ALL|AUTO}`
# is the one Var shape found in this codebase's fixtures whose casing is both
# (a) source-preserved rather than parser-normalized (unlike, e.g.,
# `GrantPrivilege`'s Var, which sqlglot uppercases at parse time regardless of
# source case) and (b) drawn from a fixed, closed keyword set rather than
# arbitrary user data, so it is safe to casefold, but only when it appears as
# the named arg of one of these specific parent node types, never as a global
# Var.this rule. See test_ast_equal_rejects_quoted_var_case for the case this
# guards against.
_CASEFOLD_VAR_ARGS = {
    exp.DistStyleProperty: ("this",),
    # `WHEN MATCHED THEN DELETE`: the whole action is a keyword, and sqlglot
    # keeps it as a Var carrying the case it was written in. sqlalign cases it
    # like any other keyword, so without this the cased output compares unequal
    # and every MERGE with a DELETE branch declined.
    exp.When: ("then",),
}


def _casefold_outside_quotes(text: str) -> str:
    """Casefold `text`, leaving the contents of '...'- and "..."-quoted regions
    (with '' / "" doubling as the escape) untouched.

    Used for exp.Command's raw fallback text: unsupported-syntax statements
    keep their entire remainder as verbatim source text, which can itself
    contain quoted identifiers or string literals whose casing is
    semantically meaningful even though the surrounding keyword text isn't.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == quote:
                    if i + 1 < n and text[i + 1] == quote:  # doubled quote: escaped, stay inside
                        out.append(text[i + 1])
                        i += 2
                        continue
                    i += 1  # unescaped: closes the quoted region
                    break
                i += 1
        else:
            out.append(ch.casefold())
            i += 1
    return "".join(out)


def _normalize(node):
    for e in node.walk():
        e.comments = None
        # `ast_equal` compares `repr`, which is stricter than sqlglot's own `==`
        #: deliberately, so a token-level difference cannot slip past. But
        # `repr` also reflects the args dict's INSERTION order, which is an
        # artefact of how a statement was written rather than what it means:
        # `GROUP BY ROLLUP(a, b), CUBE(c)` and `GROUP BY CUBE(c), ROLLUP(a, b)`
        # parse to `{rollup, cube}` and `{cube, rollup}`, and sqlglot itself
        # reports them equal. Sorting the keys removes exactly that sensitivity
        # and nothing else: the strictness stays, the false decline goes.
        e.args = dict(sorted(e.args.items()))
        if isinstance(e, exp.LanguageProperty):
            # `LANGUAGE plpgsql` and `LANGUAGE 'plpgsql'` are semantically
            # identical (the quotes are optional legacy syntax) but parse to a
            # Var vs a Literal. Canonicalize to a casefolded Var so the house
            # form (bare, lowercase: ) compares equal to a quoted input.
            name = e.this.name if e.this is not None else ""
            e.set("this", exp.var(name.casefold()))
            continue
        if isinstance(e, exp.Command):
            for arg_name in ("this", "expression"):
                value = e.args.get(arg_name)
                if isinstance(value, str):
                    e.set(arg_name, _casefold_outside_quotes(value))
            continue
        arg_names = _CASEFOLD_STR_ARGS.get(type(e))
        if arg_names:
            for arg_name in arg_names:
                value = e.args.get(arg_name)
                if isinstance(value, str):
                    e.set(arg_name, value.casefold())
        var_arg_names = _CASEFOLD_VAR_ARGS.get(type(e))
        if var_arg_names:
            for arg_name in var_arg_names:
                value = e.args.get(arg_name)
                if isinstance(value, exp.Var) and isinstance(value.this, str):
                    value.set("this", value.this.casefold())
    return node


def _statements(sql: str, dialect: str):
    """The normalized statements of `sql`, for comparison.

    `exp.Semicolon` is dropped. It is what sqlglot yields for an empty statement
    -- and, crucially, for a comment that follows the final `;`, which it hangs
    on one of these. `_normalize` strips comments, so such a node normalizes to
    nothing at all; counting it would make `SELECT a;: note` unequal to
    `select a: note ;` purely because the comment moved to the other side of
    the terminator. Since a terminator has to precede a line comment (a `;` after
    one is INSIDE it), that difference is unavoidable and it is not semantics.
    """
    return [_normalize(e) for e in sqlglot.parse(sql, read=dialect)
            if e is not None and not isinstance(e, exp.Semicolon)]


def _raw_ast_equal(a: str, b: str, dialect: str) -> bool:
    nodes_a = _statements(a, dialect)
    try:
        nodes_b = _statements(b, dialect)
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return False  # unparseable output is by definition not semantically equal
    return [repr(e) for e in nodes_a] == [repr(e) for e in nodes_b]


def _round_trips(sql: str, dialect: str) -> bool:
    """Whether sqlglot's own generator output re-parses to the same tree.

    When it does not, `ast_equal` can never pass for that statement no matter
    what sqlalign emits, so the decline is upstream rather than a renderer bug.
    """
    try:
        parsed = sqlglot.parse_one(sql, read=dialect)
        return ast_equal(sql, parsed.sql(dialect), dialect)
    except Exception:
        return True          # cannot tell; assume the fault is ours


def _first_expression(parsed):
    """The first real expression of a `sqlglot.parse` result. sqlglot yields a
    `None` entry for a statement that is only a comment or whitespace, so the
    list can be all-None even when the parse itself succeeded."""
    return next((e for e in parsed if e is not None), None)


def _dollar_create_parts(text: str, dialect: str):
    """If `text` is a single dollar-quoted CREATE FUNCTION/PROCEDURE, return its
    `(header, body, tail)` split; else None. Gates the dollar-quote-aware path."""
    parts = plpgsql.split_body(text)
    if parts is None:
        return None
    try:
        node = _first_expression(sqlglot.parse(text, read=dialect))
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return None
    return parts if plpgsql.is_dollar_create(node, dialect) else None


def ast_equal(a: str, b: str, dialect: str) -> bool:
    """AST-equality safety check. sqlglot sees a `$$…$$` body as one opaque
    string, so raw AST comparison would reject any body reformatting. When both
    sides are dollar-quoted function/procedure bodies, compare structurally
    instead: header+tail as AST, each embedded SQL statement as AST,
    and skeleton statements whitespace/case-insensitively."""
    if dialect == "tsql" and (_GO_LINE_RE.search(a) or _GO_LINE_RE.search(b)):
        # `GO` is a batch separator, not SQL, and sqlglot swallows the FOLLOWING
        # statement into it as a string literal, so a whole-file parse compares
        # nonsense. Compare batch by batch instead, and compare the separators
        # themselves so a dropped or added GO is still caught.
        batches_a, seps_a = _split_go_batches(a)
        batches_b, seps_b = _split_go_batches(b)
        if seps_a != seps_b or len(batches_a) != len(batches_b):
            return False
        return all(_raw_ast_equal(x, y, dialect)
                   for x, y in zip(batches_a, batches_b, strict=False))

    parts_a = _dollar_create_parts(a, dialect)
    parts_b = _dollar_create_parts(b, dialect)
    if parts_a is not None and parts_b is not None:
        return _plpgsql_ast_equal(parts_a, parts_b, dialect)
    return _raw_ast_equal(a, b, dialect)


def _plpgsql_ast_equal(parts_a, parts_b, dialect: str) -> bool:
    head_a, body_a, tail_a = parts_a
    head_b, body_b, tail_b = parts_b
    # header + tail form an empty-body CREATE that parses: compares signature,
    # RETURNS and LANGUAGE while ignoring the (reformatted) body.
    if not _raw_ast_equal(head_a + tail_a, head_b + tail_b, dialect):
        return False
    clauses_a = plpgsql.body_clauses(body_a)
    clauses_b = plpgsql.body_clauses(body_b)
    if len(clauses_a) != len(clauses_b):
        return False
    for x, y in zip(clauses_a, clauses_b, strict=False):
        # Skeleton/plain statements differ only by keyword case + whitespace.
        if plpgsql.norm_skeleton(x) == plpgsql.norm_skeleton(y):
            continue
        # Otherwise it is a reformatted SQL statement (token-level changes such
        # as `<>`→`!=`): compare as ASTs, dropping any leading BEGIN/DECLARE.
        if _raw_ast_equal(plpgsql.strip_struct(x), plpgsql.strip_struct(y), dialect):
            continue
        return False
    return True


def _split_prefix(text: str) -> tuple[str, str]:
    """Split leading trivia (whitespace + full comment lines/blocks) from the body.

    The prefix is everything before the first real SQL token; it is preserved
    verbatim across formatting so leading comments and blank lines survive --
    EXCEPT the horizontal whitespace immediately before that token, which the
    layout owns and re-creates. Keeping it made formatting non-idempotent for
    any style whose first line is indented: `clause_keyword_align="river"` emits
    `  SELECT`, and re-formatting that output preserved those two columns as
    prefix and then indented the river on top of them, two columns further right
    every pass. House output starts at column 0, which is why it never showed.
    """
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
        elif text.startswith("--", i):
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
        elif text.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        else:
            break
    # Give back the indent on the first token's own line: the layout re-creates
    # it, and keeping it here would compound it on every pass.
    indent = len(text[:i]) - len(text[:i].rstrip(" \t"))
    return text[:i - indent], text[i - indent:]


def _split_trailing_comment(stmt: str) -> tuple[str, str]:
    """Split a trailing `; <comment>` suffix off `stmt`.

    Returns (head, suffix) where `head` runs through the terminating ';' and
    `suffix` is everything after it (whitespace + the same-line comment),
    preserved verbatim. If there is no such trailing comment, returns (stmt, "").
    Peeling the comment lets the statement body format cleanly instead of the
    comment (which parses as a stray node) tripping the safety net.
    """
    m = _TRAILING_COMMENT_RE.search(stmt)
    if not m:
        return stmt, ""
    semi = m.start()                           # index of the ';'
    return stmt[:semi + 1], stmt[semi + 1:]


def _format_statement(stmt: str, dialect: str, style: Style) -> str:
    """Lay out one statement. Raises Unsupported for constructs we pass through."""
    stmt_head, suffix = _split_trailing_comment(stmt)
    prefix, body = _split_prefix(stmt_head)
    # `parse_dialect` layers house parse-time overrides (e.g. DATE_TRUNC's
    # case-preserving unit: see casing.py) on top of the stock dialect; only
    # this one parse (which builds the tree layout_statement renders) uses it.
    node = _first_expression(sqlglot.parse(body, read=parse_dialect(dialect)))
    if node is None:
        return stmt

    # Comment pre-pass: recover each embedded comment's original
    # style+text from `body`, reattach it to the select-list item it authorially
    # leads/trails (annotating that item's `meta` for select.py to emit), and
    # strip every `node.comments` so render_expr can't re-emit sqlglot's
    # wrong-styled version. Declines (Unsupported) any comment position it does
    # not model -> whole-statement passthrough. A no-op when there are none.
    _comments.process(node, body, dialect)

    # Layout handlers take the Width knob only; `align` is a pure emit-time
    # choice, so it is applied in render() and never threaded through handlers.
    # Comma position is relocated on the IR (each separator comma is its own
    # tagged Seg) after layout and before render, so alignment resolves against
    # the final segment text: see commas.py for why this is not a text pass.
    lines = layout_statement(node, dialect, style.width)
    lines = apply_comma_position(lines, style.comma_position)
    lines = apply_align_targets(lines, style.align_targets)
    core = render(lines, align=style.align)
    if core.endswith("\n"):
        core = core[:-1]                       # render always appends one newline

    body_stripped = body.rstrip()
    trailing_ws = body[len(body_stripped):]
    # A layout may terminate its own statement: T-SQL's BEGIN/END block closes
    # with `END;` whatever the source wrote, so appending unconditionally would
    # produce `END;;`.
    terminator = ";" if body_stripped.endswith(";") and not core.endswith(";") else ""
    if terminator:
        # A `--` comment runs to end of line, so a terminator placed after one
        # is inside it and the statement is left unterminated.
        cut = _terminator_column(core)
        return prefix + core[:cut] + terminator + core[cut:] + trailing_ws + suffix
    return prefix + core + terminator + trailing_ws + suffix


def _terminator_column(core: str) -> int:
    """Where the statement terminator belongs in `core` -- the end, unless the
    text ends INSIDE a line comment, in which case just before it."""
    from sqlalign.layout.comments import scan_comments

    for c in scan_comments(core):
        if c.style == "line" and c.end >= len(core):
            return len(core[:c.start].rstrip())
    return len(core)


def _body_is_multiline(piece: str) -> bool:
    """Whether a statement's own body spans more than one line, ignoring any
    leading blank/comment lines (a single-line statement with a leading `--`
    header is still single-line) and inline trailing comments."""
    lines = piece.split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith(("--", "/*"))):
        i += 1
    return sum(1 for line in lines[i:] if line.strip()) > 1


def _join_statements(entries, blanks=None):
    """Concatenate formatted statement pieces, normalizing the vertical gap
    between them. Inter-statement whitespace
    lives in each statement's own leading prefix (the splitter keeps the
    newline after a `;` with the *following* statement), so the gap before
    statement N is its leading run of newlines. Rule: put exactly one blank
    line between two statements only when BOTH are multi-line (#11's INSERT +
    UPDATE); otherwise preserve the input's adjacency — a single-line
    statement beside anything stays adjacent (#23–#25's grouped utility
    statements, incl. a single-line index next to a wrapped one). `entries` is
    `(text, is_statement, is_multiline)`; trivia (comment-only/whitespace,
    `is_statement` False) neither carries spacing rules nor updates the
    previous-statement state.

    An explicit `Style.blank_lines_between_statements` (`blanks`) overrides that
    rule with a fixed count between EVERY pair, regardless of shape."""
    pieces = []
    prev_seen = False                              # a real statement has been emitted
    prev_ml = None                                 # multi-line-ness of the previous real statement
    for text, is_stmt, is_ml in entries:
        if is_stmt and prev_seen:
            lead = len(text) - len(text.lstrip("\n"))
            if lead > 0:
                # The gap can sit on either side: a statement whose final line
                # is a trailing comment ends with its own newline.
                sep = ("\n" * (blanks + 1) if blanks is not None
                       else "\n\n" if prev_ml and is_ml else None)
                if sep is not None:
                    # `\n` ends the previous line; each further one is a blank line.
                    pieces[-1] = pieces[-1].rstrip("\n")
                    text = sep + text[lead:]
        pieces.append(text)
        if is_stmt:
            prev_ml, prev_seen = is_ml, True
    return "".join(pieces)


def format_sql(text: str, dialect: str = "postgres", style: Style = HOUSE) -> FormatResult:
    """Format `text`. `style` carries every knob; it is also published as the
    ambient render style for the duration of the call so `render_expr` (called
    from ~57 handler sites) can read output-spelling knobs without every handler
    signature having to carry them."""
    if dialect not in SUPPORTED_DIALECTS:
        # Refuse rather than mis-format. The handlers emit keywords chosen for
        # the verified dialects, and the AST check cannot catch one that is
        # invalid for the target engine: see SUPPORTED_DIALECTS.
        raise ValueError(
            f"unsupported dialect {dialect!r}; sqlalign supports "
            f"{', '.join(sorted(SUPPORTED_DIALECTS))}")
    with render_style(style):
        if not style.protect_templating or not templating.has_templating(text):
            return _format_all(text, dialect, style)
        # Templated SQL (dbt/Jinja) does not parse. Mask each expression with a
        # same-WIDTH placeholder so every alignment column is computed against the
        # real width, format normally, then restore. The whole pipeline: layout,
        # alignment, and the AST safety check: runs on the masked text, which is
        # ordinary SQL; masking is a bijection over identical character positions,
        # so equality of the masked forms is equality of the originals.
        try:
            masked, replacements = templating.mask(text)
        except ValueError as e:
            return FormatResult(text, [f"templating not maskable, passed through: {e}"])
        result = _format_all(masked, dialect, style)
        return FormatResult(templating.unmask(result.text, replacements), result.warnings)


def _format_all(text: str, dialect: str, style: Style) -> FormatResult:
    warnings, out, declines = [], [], []
    statements = 0
    pos = 0

    def emit(piece, is_statement):
        out.append((piece, is_statement, _body_is_multiline(piece)))

    for stmt in split_statements(text, dialect):
        try:
            if not stmt.strip().rstrip(";").strip():
                emit(stmt, False)
                continue
            if dialect == "tsql" and _GO_ONLY.fullmatch(stmt.strip()):
                emit(stmt, False)      # batch separator: trivia, preserved verbatim
                continue
            prefix, _ = _split_prefix(stmt)
            _, skip_suffix = _split_trailing_comment(stmt)
            if _SKIP_RE.search(prefix) or _SKIP_RE.search(skip_suffix):
                # Skip directive: byte-identical, no parse, no layout, no
                # warning. Counted as a decline under its own `kind`, so a report
                # can tell an opt-out from a gap in the tool.
                statements += 1
                declines.append(Decline("skipped", "-- sqlalign: skip"))
                emit(stmt, True)
                continue
            statements += 1
            snippet = stmt.strip()[:40]            # how a statement is named in a warning
            try:
                parsed = sqlglot.parse(stmt, read=dialect)
            except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
                content_off = len(stmt) - len(stmt.lstrip())
                line = text[: pos + content_off].count("\n") + 1
                warnings.append(f"passthrough (parse error line {line}): {snippet}")
                declines.append(Decline("parse", "parse error"))
                emit(stmt, True)
                continue
            else:
                if _first_expression(parsed) is None:
                    emit(stmt, False)  # comment-only / trivia: silent passthrough
                    continue
            # Defense-in-depth (FIX 1): NOTHING a single statement does may abort
            # the file. Layout + render + the AST-equality safety check are wrapped
            # so an unsupported construct, a would-change-semantics render, or an
            # outright bug each degrade to a byte-identical passthrough + a warning.
            # The safety net's guarantee (never emit semantically-changed output)
            # is preserved: a changed/failed render is passed through, never written.
            # Distinct wording per class so a real bug is visible in stderr, not
            # silently indistinguishable from an expected decline.
            try:
                if dialect == "tsql" and _TSQL_LOSSY_TYPES.search(stmt):
                    raise Unsupported("tsql: type whose spelling sqlglot cannot preserve")
                node0 = _first_expression(parsed)
                is_dollar = (plpgsql.is_dollar_create(node0, dialect)
                             and plpgsql.split_body(stmt) is not None)
                if is_dollar and not style.format_dollar_bodies:
                    # Opted out (--no-format-bodies): leave the whole procedure
                    # byte-identical. Not a decline, so no warning is emitted.
                    emit(stmt, True)
                    continue
                if is_dollar:
                    formatted = plpgsql.format_create(stmt, node0, dialect, style)
                else:
                    formatted = _format_statement(stmt, dialect, style)
                if style.keyword_case != "upper":
                    formatted = keywordcase.apply(
                        formatted,
                        keywordcase.identifier_names(stmt, dialect),
                        style.keyword_case)
                if not ast_equal(stmt, formatted, dialect):
                    raise SafetyError(f"formatting changed semantics near: {snippet}")
            except Unsupported as exc:
                # The reason rides in the message so `--report` can name the
                # construct, not just count the declines.
                warnings.append(
                    f"unsupported construct ({exc}), passed through: {snippet}")
                declines.append(Decline("unsupported", str(exc)))
                emit(stmt, True)
                continue
            except SafetyError:
                # If sqlglot cannot round-trip the input through its own
                # generator, no formatter could satisfy the check and the fault
                # is upstream: report it as such rather than as a semantic
                # change, which means sqlalign's renderer is wrong.
                if not _round_trips(stmt, dialect):
                    warnings.append(
                        "sqlglot cannot round-trip this statement, passed through "
                        f"unformatted: {snippet}")
                    declines.append(Decline("upstream", "sqlglot round-trip is unstable"))
                    emit(stmt, True)
                    continue
                warnings.append(
                    f"formatting would change semantics, passed through unformatted: {snippet}")
                declines.append(Decline("safety", "output would differ semantically"))
                emit(stmt, True)
                continue
            except Exception as exc:  # a real bug: surface it, but keep formatting the file
                warnings.append(
                    "internal formatter error, passed through (please report): "
                    f"{type(exc).__name__}: {exc}")
                declines.append(Decline("error", type(exc).__name__))
                emit(stmt, True)
                continue
            emit(formatted, True)
        finally:
            pos += len(stmt)
    return FormatResult(_join_statements(out, style.blank_lines_between_statements),
                        warnings, statements, tuple(declines))
