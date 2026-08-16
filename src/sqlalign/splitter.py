"""Statement splitting: the one place a file is cut into statements.

Every split here is LOSSLESS: the returned parts concatenate back to the input
byte for byte, whitespace and comments included, because a statement the
engine declines is emitted from its part verbatim, so a part that lost
a character would corrupt the passthrough.
"""
import re

# Dollar-quote tag: bare `$$`, or `$tag$` where tag starts with a letter/underscore
# and may contain digits thereafter (e.g. `$fn1$`): matches Postgres's identifier
# rules for dollar-quote tags, which allow digits after the first character.
# Public because plpgsql.py splits the same bodies and must agree exactly.
DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")

_IDENT_CHARS = re.compile(r"[A-Za-z0-9_]")


# A T-SQL batch separator: a line whose entire content is `GO`, optionally with a
# repeat count. It is a client-tool directive, not SQL: sqlglot cannot parse it,
# and worse, it SWALLOWS the following statement into the GO command as a string
# literal (`GO\nSELECT 2` parses as one Command). Batches must therefore be split
# before anything is parsed.
_GO_LINE = re.compile(r"^[ \t]*GO(?:[ \t]+\d+)?[ \t]*$", re.IGNORECASE | re.MULTILINE)


def split_statements(text: str, dialect: str = "postgres") -> list[str]:
    """Split on top-level semicolons; lossless (parts concatenate to the input).

    Under T-SQL a standalone `GO` line additionally ends a batch, and is returned
    as its own part so the caller can pass it through verbatim.
    """
    if dialect == "tsql":
        text_parts = _split_tsql_blocks(text)
        if text_parts is not None:
            return text_parts
        if _GO_LINE.search(text):
            parts: list[str] = []
            pos = 0
            for match in _GO_LINE.finditer(text):
                before = text[pos:match.start()]
                if before:
                    parts.extend(split_statements(before))
                parts.append(text[match.start():match.end()])   # the GO line itself
                pos = match.end()
            if pos < len(text):
                parts.extend(split_statements(text[pos:]))
            return parts
    return _split_on_semicolons(text)


# The routine-defining statements whose body is a BEGIN...END block. Only these
# get block tracking, deliberately: a general "count BEGIN and END" scan is unsafe
# because `CASE ... END` shares the END keyword, and `BEGIN TRANSACTION` opens a
# block that never has a matching END at all. Confining the scan to a routine body
# keeps both hazards out of ordinary statements.
_ROUTINE_START = re.compile(
    r"\bCREATE\s+(?:OR\s+ALTER\s+)?(?:PROCEDURE|PROC|FUNCTION|TRIGGER)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _split_tsql_blocks(text: str) -> list[str] | None:
    """Split `text` keeping each `CREATE PROCEDURE ... BEGIN ... END` whole.

    Returns None when there is no routine definition, so the ordinary scanner
    handles the text unchanged. A routine's body contains semicolons that must NOT
    end the statement, which is exactly what the plain scanner would do.

    `END` is ambiguous: it also closes `CASE` — so a small stack records what each
    opener was and only a `BEGIN` popped off it ends the block.
    """
    if not _ROUTINE_START.search(text):
        return None
    parts: list[str] = []
    pos = 0
    while True:
        match = _ROUTINE_START.search(text, pos)
        if match is None:
            if pos < len(text):
                parts.extend(_split_on_semicolons(text[pos:]))
            return parts
        if match.start() > pos:
            parts.extend(_split_on_semicolons(text[pos:match.start()]))
        end = _routine_end(text, match.start())
        parts.append(text[match.start():end])
        pos = end


def _routine_end(text: str, start: int) -> int:
    """Index just past the routine beginning at `start`: the `END` that closes its
    outermost `BEGIN`, plus a trailing `;` if present. Falls back to end-of-text
    for an unterminated routine, which then simply fails to parse and passes
    through."""
    stack: list[str] = []
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"', "[") or text.startswith("--", i) or text.startswith("/*", i):
            i = _skip_opaque(text, i)          # never read keywords out of these
            continue
        word_match = _WORD_RE.match(text, i)
        if word_match is None:
            i += 1
            continue
        word = word_match.group(0).upper()
        i = word_match.end()
        if word == "CASE":
            stack.append("CASE")
        elif word == "BEGIN":
            # BEGIN TRANSACTION/TRAN opens no block that an END will close.
            following = _WORD_RE.match(text, _skip_spaces(text, i))
            if following and following.group(0).upper() in ("TRANSACTION", "TRAN"):
                continue
            stack.append("BEGIN")
        elif word == "END" and stack:
            closed = stack.pop()
            # Only the OUTERMOST BEGIN ends the routine; an END that closed a
            # CASE, or one that leaves the stack non-empty, is interior.
            if closed == "BEGIN" and not stack:
                tail = _skip_spaces(text, i)
                return tail + 1 if tail < n and text[tail] == ";" else i
    return n


def _skip_spaces(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _skip_opaque(text: str, i: int) -> int:
    """Advance past a string, quoted/bracketed identifier, or comment, so a
    keyword-looking word inside one is never mistaken for a BEGIN/END/CASE."""
    n = len(text)
    ch = text[i]
    if ch == "'":
        i += 1
        while i < n:
            if text[i] == "'" and not (i + 1 < n and text[i + 1] == "'"):
                return i + 1
            i += 2 if text[i] == "'" else 1
        return n
    if ch == '"':
        j = text.find('"', i + 1)
        return n if j == -1 else j + 1
    if ch == "[":
        i += 1
        while i < n:
            if text[i] == "]":
                if i + 1 < n and text[i + 1] == "]":
                    i += 2
                    continue
                return i + 1
            i += 1
        return n
    if text.startswith("--", i):
        j = text.find("\n", i)
        return n if j == -1 else j
    if text.startswith("/*", i):
        j = text.find("*/", i)
        return n if j == -1 else j + 2
    return i + 1


def _split_on_semicolons(text: str) -> list[str]:
    parts: list[str] = []
    start = i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "'":                      # standard or E-prefixed string
            # An E-string (`E'...'` / `e'...'`) additionally honors backslash
            # escapes (`\'`, `\\`); a bare string only understands `''` doubling.
            # Only treat a preceding e/E as the E-prefix if it isn't itself the
            # tail of a longer identifier (e.g. `CASE'x'` is CASE, not E'x').
            prev = text[i - 1] if i > 0 else ""
            prev2 = text[i - 2] if i > 1 else ""
            is_estring = prev in "eE" and not _IDENT_CHARS.match(prev2)
            i += 1
            while i < n:
                if is_estring and text[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if text[i] == "'" and not (i + 1 < n and text[i + 1] == "'"):
                    break
                i += 2 if text[i] == "'" else 1
            i += 1
        elif ch == '"':                    # quoted identifier
            close = text.find('"', i + 1)
            i = n if close == -1 else close + 1
        elif ch == "[":
            # T-SQL bracket-quoted identifier: [my col], and `]]` escapes a literal
            # `]` inside one. Without this a bracketed name containing a semicolon
            # (`[my;col]`) splits mid-identifier: lossless, so the file survives,
            # but every fragment then fails to parse and the file goes unformatted.
            i += 1
            while i < n:
                if text[i] == "]":
                    if i + 1 < n and text[i + 1] == "]":   # ]] -> escaped
                        i += 2
                        continue
                    i += 1
                    break
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
        elif ch == "$" and (m := DOLLAR_TAG.match(text, i)):
            tag = m.group(0)
            end = text.find(tag, m.end())
            i = n if end == -1 else end + len(tag)
        elif ch == ";":
            i += 1
            while i < n and text[i] in " \t":   # keep same-line trailer with the statement
                i += 1
            if i < n and text.startswith("--", i):
                nl = text.find("\n", i)
                i = n if nl == -1 else nl + 1
            parts.append(text[start:i])
            start = i
        else:
            i += 1
    if start < n:
        remainder = text[start:]
        # Trailing whitespace after the last `;` belongs to the statement before
        # it, not to a part of its own: a whitespace-only part parses to nothing
        # and would be emitted as a spurious empty statement.
        if remainder.strip() or not parts:
            parts.append(remainder)
        else:
            parts[-1] += remainder
    return parts
