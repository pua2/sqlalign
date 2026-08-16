"""Dollar-quoted plpgsql function/procedure bodies.

sqlglot parses a `CREATE FUNCTION … AS $$ … $$` as an `exp.Create` whose
`expression` is one opaque `exp.Heredoc` string: it does not look inside the
body. This module formats that body: the `CREATE` header is rebuilt from the
AST (signature, `RETURNS`, `LANGUAGE`), while the body between the `$$` tags is
split into plpgsql statements and laid out per §3.5: `DECLARE`/`BEGIN`/`END`
skeleton, `IF … THEN … [ELSE …] END IF` geometry, embedded SQL statements
routed back through the main engine, and `RAISE`/`GET DIAGNOSTICS`/`:=`/`RETURN`
as plain keyword-cased statements. Anything unmodeled raises `Unsupported`, so
the whole statement degrades to a byte-identical passthrough.

To avoid an import cycle, this module never imports `formatter` at module
scope: the safety helpers (`split_body`, `body_clauses`, `norm_skeleton`,
`strip_struct`) are pure text functions `formatter.ast_equal` calls, and the
one place that needs the main engine (`_render_sql_stmt`) imports it lazily.
"""
import re
from functools import cache

import sqlglot
from sqlglot import exp

from sqlalign.casing import render_expr
from sqlalign.layout import Unsupported
from sqlalign.splitter import DOLLAR_TAG, split_statements

# `DOLLAR_TAG` is imported, not re-declared: this module and the splitter cut the
# same bodies, so a drift between two copies of the tag pattern would split a
# body at a point the other side does not treat as opaque.

# Body statements that route through the main SQL engine; everything else in a
# plpgsql body is a "plain" statement rendered by keyword-casing.
_SQL_STMT_TYPES = (exp.Select, exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.SetOperation)

# plpgsql keywords sqlglot's tokenizer does not list as single-word keywords
# (verified against pinned v30.14); union'd with the tokenizer set for casing.
_PLPGSQL_KW = {"RETURN", "RAISE", "NOTICE", "WARNING", "EXCEPTION", "INFO", "LOG",
               "DEBUG", "GET", "DIAGNOSTICS", "PERFORM", "ASSERT"}

# Create args this module models; any other truthy arg declines to passthrough.
_CREATE_FN_OK = {"this", "kind", "replace", "expression", "properties"}


# ---- dollar-quote scanning (pure text; used by layout AND the safety net) ----

def split_body(stmt: str):
    """Split `stmt` at its dollar-quoted body into `(header, body, tail)`.

    `header` runs through the opening `$tag$`, `body` is the text between the
    tags, `tail` is the closing `$tag$` onward. Lossless: the three concatenate
    back to `stmt`. Returns `None` when there is no dollar-quoted region.
    """
    m = DOLLAR_TAG.search(stmt)
    if not m:
        return None
    tag = m.group(0)
    close = stmt.find(tag, m.end())
    if close == -1:
        return None
    return stmt[:m.end()], stmt[m.end():close], stmt[close:]


# The two body languages whose contents sqlalign lays out. `plpgsql` is a
# DECLARE/BEGIN/END block; `sql` is a bare statement list with no block at all,
# which is why it needs its own branch rather than a wider guard. Everything
# else (plpython, plperl, ...) is not SQL and is declined at the header.
_BODY_LANGUAGES = ("plpgsql", "sql")


def _language_name(node) -> str:
    props = node.args.get("properties")
    for p in (props.expressions if props else []):
        if isinstance(p, exp.LanguageProperty):
            return p.this.name if hasattr(p.this, "name") else str(p.this)
    return ""


def body_language(node) -> str | None:
    """`"plpgsql"` / `"sql"` for a body this module lays out, else None."""
    lang = _language_name(node).lower()
    return lang if lang in _BODY_LANGUAGES else None


def is_dollar_create(node, dialect: str) -> bool:
    """Whether `node` is a `CREATE FUNCTION/PROCEDURE` with a dollar-quoted body."""
    return (isinstance(node, exp.Create)
            and (node.args.get("kind") or "").upper() in ("FUNCTION", "PROCEDURE")
            and isinstance(node.args.get("expression"), exp.Heredoc))


def body_clauses(body: str) -> list[str]:
    """Split a plpgsql body into `;`-terminated clauses (string/comment/dollar
    aware, via the shared splitter), stripped and with the `;` removed; empty
    clauses dropped. `BEGIN`/`IF … THEN` keywords stay glued to the statement
    that follows them, so the input body and the formatted body split into the
    same clause sequence — which the safety net relies on."""
    out = []
    for part in split_statements(body):
        c = part.strip()
        if c.endswith(";"):
            c = c[:-1].strip()
        if c:
            out.append(c)
    return out


# ---- safety-net text normalization ----

def norm_skeleton(text: str) -> str:
    """Casefold and collapse whitespace, both only OUTSIDE quoted regions, so
    string-literal interiors stay byte-exact. Two clauses equal under this norm
    differ by at most keyword casing and whitespace: a semantically inert
    difference the formatter is allowed to make to a skeleton statement."""
    out, i, n, prev_space = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            prev_space = False
            out.append(ch)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == ch:
                    if i + 1 < n and text[i + 1] == ch:   # doubled quote: escaped
                        out.append(text[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        elif ch.isspace():
            if not prev_space:
                out.append(" ")
                prev_space = True
            i += 1
        else:
            out.append(ch.casefold())
            prev_space = False
            i += 1
    return "".join(out).strip()


_STRUCT_LEAD = re.compile(r"^(?:declare|begin)\b\s*", re.IGNORECASE)


def strip_struct(text: str) -> str:
    """Drop a leading `DECLARE`/`BEGIN` keyword (the only skeleton words that
    glue to a following statement) so the remainder can be AST-compared."""
    return _STRUCT_LEAD.sub("", text, count=1)


# ---- keyword casing for plain statements ----

@cache
def _keywords(dialect: str) -> frozenset[str]:
    """Every single-word keyword of `dialect`. Cached: building it instantiates
    a Tokenizer, and every plain body statement asks for it."""
    from sqlglot.tokens import Tokenizer
    return frozenset(k for k in Tokenizer(dialect=dialect).KEYWORDS if " " not in k) | _PLPGSQL_KW


def _tokenize_ws(text: str) -> list[str]:
    """Whitespace-split `text`, treating a quoted span as opaque (never split
    inside it), so string literals survive keyword casing intact."""
    tokens, cur, i, n = [], [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            if cur:
                tokens.append("".join(cur))
                cur = []
            i += 1
        elif ch in ("'", '"'):
            cur.append(ch)
            i += 1
            while i < n:
                cur.append(text[i])
                if text[i] == ch:
                    if i + 1 < n and text[i + 1] == ch:
                        cur.append(text[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        else:
            cur.append(ch)
            i += 1
    if cur:
        tokens.append("".join(cur))
    return tokens


def _upper_kw(text: str, dialect: str) -> str:
    """Uppercase keyword tokens (identifiers and string literals preserved),
    normalizing runs of whitespace to single spaces."""
    kw = _keywords(dialect)
    return " ".join(t.upper() if t.upper() in kw else t for t in _tokenize_ws(text))


def _render_plain(text: str, dialect: str) -> str:
    text = text.strip()
    if text.lower().startswith("get diagnostics"):
        return _render_get_diagnostics(text)
    return _upper_kw(text, dialect)


def _render_get_diagnostics(text: str) -> str:
    """`get diagnostics <var> = <item>` → `GET DIAGNOSTICS <var> = <ITEM>`: the
    assignment target is a variable (preserved) but the right side is a
    diagnostic keyword (uppercased). The two can be the same word (`row_count`
    vs `ROW_COUNT`), so this cannot go through generic keyword casing."""
    body = text[len("get diagnostics"):].strip()
    if body.count("=") != 1:
        raise Unsupported("plpgsql GET DIAGNOSTICS: unexpected form")
    lhs, rhs = body.split("=")
    return "GET DIAGNOSTICS " + lhs.strip() + " = " + rhs.strip().upper()


# ---- CREATE header (rebuilt from the AST) ----

def _guard_create(node) -> None:
    for name, value in node.args.items():
        if value in (None, [], False):
            continue
        if name not in _CREATE_FN_OK:
            raise Unsupported(f"plpgsql CREATE arg: {name}")
    if not isinstance(node.this, exp.UserDefinedFunction):
        raise Unsupported("plpgsql: non-UDF signature")


def _header_lines(node, dialect: str, tag: str) -> list[str]:
    _guard_create(node)
    kind = node.args["kind"].upper()                     # FUNCTION | PROCEDURE
    head = "CREATE"
    if node.args.get("replace"):
        head += " OR REPLACE"
    head += f" {kind} " + render_expr(node.this, dialect)
    lines = [head]
    props = node.args.get("properties")
    returns = language = None
    for p in (props.expressions if props else []):
        if isinstance(p, exp.ReturnsProperty):
            returns = p
        elif isinstance(p, exp.LanguageProperty):
            language = p
        else:
            raise Unsupported(f"plpgsql: property {type(p).__name__}")
    if returns is not None:
        lines.append("RETURNS " + render_expr(returns.this, dialect))
    if language is None:
        raise Unsupported("plpgsql: missing LANGUAGE")
    lang = body_language(node)
    if lang is None:
        raise Unsupported(f"plpgsql: language {_language_name(node)!r}")
    lines.append(f"LANGUAGE {lang}")             # canonical bare form
    lines.append("AS " + tag)
    return lines


# ---- body layout ----

def _first_word(clause: str) -> str:
    """The clause's leading keyword, lowercased (`""` for a blank clause)."""
    words = clause.split(None, 1)
    return words[0].lower() if words else ""


def _norm(clause: str) -> str:
    """Lowercased with whitespace runs collapsed, for comparing a whole clause
    against a fixed skeleton word (`end`, `end if`)."""
    return " ".join(clause.lower().split())


def _render_sql_stmt(raw: str, dialect: str, style) -> str:
    """Route an embedded SQL statement through the main engine (identical
    formatting to top level). A construct the engine declines passes through
    verbatim per §6 — the whole body's safety check still validates it."""
    from sqlalign.formatter import _format_statement  # lazy: breaks import cycle
    try:
        return _format_statement(raw + ";", dialect, style)
    except Unsupported:
        return raw.strip() + ";"


def _is_sql(raw: str, dialect: str) -> bool:
    try:
        node = sqlglot.parse_one(raw, read=dialect)
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return False
    return isinstance(node, _SQL_STMT_TYPES)


def _render_stmt(raw: str, dialect: str, style) -> str:
    # A non-SQL statement is keyword-cased as a plain statement. An unmodeled
    # control construct (e.g. `FOR … LOOP`) is plain-rendered here but declined
    # downstream: its `END LOOP`/etc. fails the bare-END / END-IF structural
    # guards, so the whole body degrades to passthrough (and the safety net is
    # the final backstop). No fixture exercises those constructs.
    if _is_sql(raw, dialect):
        return _render_sql_stmt(raw, dialect, style)
    return _render_plain(raw, dialect) + ";"


def _render_branch(raw: str, dialect: str) -> str:
    """A THEN/ELSE branch body: a single plain statement on the same line as
    the keyword. A SQL statement there (multi-line geometry) is declined."""
    if _is_sql(raw, dialect):
        raise Unsupported("plpgsql IF branch: SQL statement")
    return _render_plain(raw, dialect) + ";"


def _render_declare(decls: list[str], dialect: str) -> str:
    # v1 models a single declaration (both fixtures). Multi-declaration column
    # alignment is unexercised, so it declines rather than guess.
    if len(decls) != 1:
        raise Unsupported("plpgsql: multiple DECLARE entries")
    parts = decls[0].split(None, 1)
    if len(parts) != 2:
        raise Unsupported("plpgsql DECLARE: unrecognized declaration")
    name, rest = parts
    return f"DECLARE {name} {_upper_kw(rest, dialect)};"


def _consume_if(stmts: list[str], j: int, dialect: str):
    """Parse an `IF … THEN … [ELSE …] END IF` starting at `stmts[j]`; return
    the rendered block and the index just past `END IF`.

    Takes no `style`: every branch body here is a plain (non-SQL) statement --
    a SQL statement inside a branch is declined by `_render_branch`: so
    nothing in a rendered IF block goes back through the layout engine.
    """
    head = stmts[j]
    mt = re.search(r"\bthen\b", head, re.IGNORECASE)
    if not mt:
        raise Unsupported("plpgsql IF: no THEN")
    cond = head[len("if"):mt.start()].strip()
    first = head[mt.end():].strip()
    then_stmts = [first] if first else []
    j += 1
    m = len(stmts)
    while j < m and _first_word(stmts[j]) not in ("else", "elsif", "end"):
        then_stmts.append(stmts[j])
        j += 1
    else_stmts = []
    if j < m and _first_word(stmts[j]) == "else":
        rest = stmts[j][len("else"):].strip()
        if rest:
            else_stmts.append(rest)
        j += 1
        while j < m and _first_word(stmts[j]) not in ("elsif", "end"):
            else_stmts.append(stmts[j])
            j += 1
    if j < m and _first_word(stmts[j]) == "elsif":
        raise Unsupported("plpgsql ELSIF")
    if j >= m or _norm(stmts[j]) != "end if":
        raise Unsupported("plpgsql IF: no END IF")
    j += 1
    if len(then_stmts) != 1 or len(else_stmts) > 1:
        raise Unsupported("plpgsql IF: multi-statement branch")
    for s in then_stmts + else_stmts:
        if _first_word(s) in ("if", "begin", "declare"):
            raise Unsupported("plpgsql: nested block in IF")
    lines = ["IF " + _render_plain(cond, dialect),
             "  THEN " + _render_branch(then_stmts[0], dialect)]
    if else_stmts:
        lines.append("  ELSE " + _render_branch(else_stmts[0], dialect))
    lines.append("END IF;")
    return "\n".join(lines), j


def _parse_body_elements(body: str, dialect: str, style, language="plpgsql") -> list[str]:
    cls = body_clauses(body)
    if language == "sql":
        # A `LANGUAGE sql` body is a statement list and nothing else: no
        # DECLARE, no BEGIN, no END. Each statement goes through the same
        # renderer a plpgsql body's statements do, so the two cannot drift.
        return [_render_stmt(c, dialect, style) for c in cls]
    elements: list[str] = []
    i, n = 0, len(cls)
    if i < n and _first_word(cls[i]) == "declare":
        decls = [cls[i][len("declare"):].strip()]
        i += 1
        while i < n and _first_word(cls[i]) != "begin":
            decls.append(cls[i].strip())
            i += 1
        elements.append(_render_declare(decls, dialect))
    if i >= n or _first_word(cls[i]) != "begin":
        raise Unsupported("plpgsql: expected BEGIN")
    elements.append("BEGIN")
    begin_rest = cls[i][len("begin"):].strip()
    i += 1
    stmts = ([begin_rest] if begin_rest else []) + cls[i:]
    j, m = 0, len(stmts)
    while j < m and _first_word(stmts[j]) != "end":
        if _first_word(stmts[j]) == "if":
            block, j = _consume_if(stmts, j, dialect)
            elements.append(block)
        else:
            elements.append(_render_stmt(stmts[j], dialect, style))
            j += 1
    if j >= m or _norm(stmts[j]) != "end":     # bare END only (no label/args)
        raise Unsupported("plpgsql: expected END")
    if j != m - 1:
        raise Unsupported("plpgsql: statements after END")
    elements.append("END;")
    return elements


def layout_body(body: str, dialect: str, style, language="plpgsql") -> str:
    """Format a plpgsql body: elements at column 1, `body_blank_lines` between.

    The body has its own vertical rhythm, separate from the one BETWEEN
    statements: `blank_lines_between_statements` never reached in here, so this
    was a hardcoded single blank line whatever that was set to.
    """
    separator = "\n" + "\n" * max(style.body_blank_lines, 0) if style else "\n\n"
    return separator.join(_parse_body_elements(body, dialect, style, language))


def format_create(stmt: str, node, dialect: str, style) -> str:
    """Format a whole `CREATE FUNCTION/PROCEDURE … $$body$$;` statement,
    preserving any leading comment/whitespace prefix and trailing whitespace."""
    from sqlalign.formatter import _split_prefix  # lazy: breaks import cycle
    prefix, rest = _split_prefix(stmt)
    core_stmt = rest.rstrip()
    trailing_ws = rest[len(core_stmt):]
    parts = split_body(core_stmt)
    if parts is None:
        raise Unsupported("plpgsql: no dollar-quoted body")
    tag = DOLLAR_TAG.search(core_stmt).group(0)
    _, body_raw, _tail = parts
    header = "\n".join(_header_lines(node, dialect, tag))
    body = layout_body(body_raw, dialect, style, body_language(node) or "plpgsql")
    core = f"{header}\n\n{body}\n{tag};"
    return prefix + core + trailing_ws
