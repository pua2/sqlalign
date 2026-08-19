"""Faithful comment engine.

sqlglot's AST is lossy for comments: it discards the `--` vs `/* */` **style**,
strips the delimiters from the text, and attaches every comment to a node by raw
source *proximity* rather than authorial position (#12's `/* legacy field … */`
binds to `signup_date` but authorially leads `ltv`). The safety net cannot catch
any of this: `ast_equal` excludes comments (formatter.py `_normalize` sets
`comments=None`) — so a dropped, restyled, or relocated comment is INVISIBLE to
it and the golden byte-comparison is the only guard. This engine therefore
reproduces comments faithfully or DECLINES (`Unsupported` → byte-identical
passthrough); it never emits a best-effort comment.

`process(node, source, dialect)` runs once, before layout, as a pre-pass over the
parsed statement:

1. **Style/text recovery**: `scan_comments` re-scans the RAW statement (a
   comment/string/dollar-quote-aware scanner mirroring splitter.py) for every
   comment token in source order, capturing exact style (`line`|`block`), exact
   inner text, the verbatim rendered form, and source offsets. sqlglot is used
   only as a cross-check (its stored, delimiter-stripped texts must match the
   scanned inner texts as a multiset, else decline).
2. **Authorial reattachment** — instead of trusting sqlglot's proximity binding,
   each comment is placed against the SELECT list's own source geometry: the
   top-level (depth-0) commas partition the list into one slot per item, and a
   comment falls in exactly one slot. Within its slot a comment is **leading**
   (only whitespace/other-comments before it) or **trailing** (only
   whitespace/other-comments after it). This makes the `/* legacy … */` case
   fall in `ltv`'s slot (after `signup_date`'s comma, before `round(`), i.e.
   leading `ltv`, with no dependence on sqlglot's wrong binding.
3. **Emission map + strip** — each item node is annotated (`meta["sqlalign_lead"]`
   / `meta["sqlalign_trail"]` = the verbatim comment) and ALL `node.comments` are
   stripped, so `render_expr` can never re-emit sqlglot's wrong-styled version.
   select.py's `select_items` reads the meta and emits the comment Segs (leading
   before the expression: measured for the `AS` column; trailing after the alias
   — untagged, one space after content, never in an alignment column).

The same treatment now covers `WHERE`/`HAVING`/`QUALIFY`, because they have the
property that made the select list modellable at all: the source geometry lines
up with the layout. Depth-0 commas partition the select list into one slot per
item and the layout puts one ROW per item; depth-0 `AND`/`OR` partition a
predicate the same way, and `condition_block` puts one row per condition. So a
comment lands on the row the author wrote it against, and `_slots_from` takes
the separator as a parameter rather than the two being written twice.

`GROUP BY` / `ORDER BY` and `FROM` came next, each needing only its own
separator: commas for the first pair, and for FROM a comma OR the start of a
join clause: at `LEFT`, not at `JOIN`, so a comment written before `LEFT JOIN`
stays with the row above rather than jumping a line. GROUP BY and ORDER BY are
laid out from rendered TEXT rather than from nodes, so grouporder.py lifts the
annotations off and hands them to `comma_clause`.

What still raises: a comment BURIED inside an expression (it sits between two
things the layout renders as one segment, so there is no row for it), and a
POSITIONAL `GROUP BY 1, 2`, whose terms share a single line. As before, every
position this engine does not model raises `Unsupported`, so the tool only ever
claims positions it reproduces byte-exact.

The emission rule is the same everywhere and worth stating once: a `--` or
multi-line comment cannot share a row with content that FOLLOWS it, so it takes
the end of the row above. That is both where the author wrote it and the only
idempotent placement: on a row of its own it re-parses as trailing the row
above, and would move on a second run.
"""
import re
from collections import Counter
from dataclasses import dataclass

from sqlglot import exp

from sqlalign.layout import Unsupported
from sqlalign.splitter import DOLLAR_TAG

# Imported rather than mirrored. The copy this replaces was a second ASCII-only
# regex that had to be found and widened separately, which is exactly how the
# two drift.
_DOLLAR = DOLLAR_TAG
_IDENT_CHARS = re.compile(r"[A-Za-z0-9_]")

# SELECT-list terminators: the first depth-0 occurrence of any of these (as a
# whole word) ends the select list. Bounds the last item's slot so a comment in
# a following clause is correctly seen as OUTSIDE the select list.
_CLAUSE_KEYWORDS = frozenset({
    "from", "where", "group", "having", "order", "limit", "offset", "window",
    "union", "intersect", "except", "fetch", "into", "qualify", "for",
})


@dataclass(frozen=True)
class Comment:
    style: str      # "line" (`--`) | "block" (`/* */`)
    text: str       # inner text, delimiters stripped (matches sqlglot's node.comments)
    verbatim: str   # exact source form, e.g. "-- rounded for reporting"
    start: int      # source offset of the first delimiter char
    end: int        # source offset one past the comment


def scan_comments(text: str) -> list[Comment]:
    """Every comment token in `text`, in source order, with style/text/verbatim.

    String-, quoted-identifier-, and dollar-quote-aware (a `--`/`/* */` inside a
    literal is not a comment), mirroring splitter.py's scanner so the two never
    disagree about where a comment begins.
    """
    out: list[Comment] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":                          # standard or E-prefixed string
            i = _skip_string(text, i)
        elif ch == '"':                        # quoted identifier
            i = text.find('"', i + 1) + 1 or n
        elif text.startswith("--", i):
            nl = text.find("\n", i)
            end = n if nl == -1 else nl
            out.append(Comment("line", text[i + 2:end], text[i:end], i, end))
            i = end
        elif text.startswith("/*", i):
            end = _skip_block(text, i)
            out.append(Comment("block", text[i + 2:end - 2], text[i:end], i, end))
            i = end
        elif ch == "$" and (m := _DOLLAR.match(text, i)):
            i = _skip_dollar(text, m)
        else:
            i += 1
    return out


def process(node: exp.Expression, source: str, dialect: str) -> None:
    """Recover, reattach, and strip `source`'s comments (see module docstring).

    Mutates `node`: annotates each select-list item with its leading/trailing
    verbatim comment via `meta`, then strips every `node.comments`. Raises
    `Unsupported` for any comment position this engine does not model, so the
    caller passes the whole statement through byte-identical.
    """
    attached = [c for n in node.walk() if n.comments for c in n.comments]
    if not attached:
        return

    scanned = scan_comments(source)
    # Cross-check against sqlglot: the scanned inner texts and the attached texts
    # must be the same multiset. A mismatch means a comment sits somewhere the
    # scan and sqlglot disagree about (or one dropped it): decline rather than
    # risk a wrong reproduction.
    if Counter(c.text for c in scanned) != Counter(attached):
        raise Unsupported("comment recovery mismatch")

    if not isinstance(node, exp.Select):
        raise Unsupported("comments outside a plain SELECT")

    groups = _comment_groups(node, source)

    lead: dict[int, str] = {}
    trail: dict[int, str] = {}
    for c in scanned:
        slots, targets, label = _group_of(c, groups)
        if slots is None:
            raise Unsupported("comment outside a modelled clause")
        idx = _slot_of(c, slots)
        before = _only_trivia(source, slots[idx][0], c.start, scanned)
        after = _only_trivia(source, c.end, slots[idx][1], scanned)
        # A Seg carrying a newline would corrupt align.py's column math, but
        # only where the comment sits inside a row's content. Both placements
        # this engine produces put it at the end of a row, after every segment
        # in an alignment column, so nothing measurable follows the newline.
        key = (label, idx)
        if before:
            if key in lead:
                raise Unsupported("comment: multiple leading comments")
            lead[key] = c.verbatim
        elif after:
            if key in trail:
                raise Unsupported("comment: multiple trailing comments")
            trail[key] = c.verbatim
        else:
            raise Unsupported("comment buried in expression")

    for _slots, targets, label in groups:
        for i, target in enumerate(targets):
            if (label, i) in lead:
                target.meta["sqlalign_lead"] = lead[(label, i)]
            if (label, i) in trail:
                target.meta["sqlalign_trail"] = trail[(label, i)]
    for n in node.walk():
        n.comments = None


# The clauses whose rows a comment can be attached to. The select list splits on
# commas and the predicate clauses on their AND/OR boundaries: in both cases
# exactly the boundaries the layout puts one ROW per, which is what makes a
# comment reproducible: it lands on the row the author wrote it against.
_PREDICATE_CLAUSES = ("where", "having", "qualify")


def _comment_groups(node, source):
    """`[(slots, target_nodes, label)]` for every clause a comment may sit in.

    A group is only offered when its slot count matches its node count. Where it
    does not, the source geometry and the parsed shape disagree and there is no
    safe mapping: the group is dropped, and a comment inside it then finds no
    home and declines, which is the whole contract of this module.
    """
    from sqlalign.layout.conditions import split_conjunction

    groups = []
    slots = _select_list_slots(source)
    if len(slots) == len(node.expressions):
        groups.append((slots, list(node.expressions), "select"))

    for keyword in _PREDICATE_CLAUSES:
        clause = node.args.get(keyword)
        if clause is None:
            continue
        conditions, _joiner = split_conjunction(clause.this)
        slots = _where_slots(source, keyword)
        if len(slots) == len(conditions):
            groups.append((slots, conditions, keyword))

    # GROUP BY / ORDER BY are comma lists like the select list. Their terms are
    # laid out from rendered TEXT rather than from nodes, so grouporder.py lifts
    # the annotations off and hands them to `comma_clause`: the nodes are
    # still where they are carried.
    for keyword, arg, terms in (
            ("group by", "group", _group_terms(node)),
            ("order by", "order", _order_terms(node))):
        if not terms:
            continue
        slots = _comma_clause_slots(source, keyword)
        if len(slots) == len(terms):
            groups.append((slots, terms, arg))

    # FROM is one row per table reference, split on commas and on the START of a
    # join clause: at `LEFT`, not at `JOIN`, so a comment written before
    # `LEFT JOIN` stays with the row above it rather than jumping a line.
    refs = _from_refs(node)
    if refs:
        slots = _from_slots(source)
        if len(slots) == len(refs):
            groups.append((slots, refs, "from"))
    return groups


def _from_refs(node):
    """The FROM clause's table references, in the order they are laid out."""
    frm = node.args.get("from_")
    if frm is None:
        return []
    return [frm.this, *(j.this for j in node.args.get("joins") or [])]


def _from_slots(source: str) -> list[tuple[int, int]]:
    i = _skip_ws_comments(source, 0)
    n = len(source)
    while i < n:
        if (_IDENT_CHARS.match(source[i]) and _word_at(source, i, "from")
                and (i == 0 or not _IDENT_CHARS.match(source[i - 1]))):
            return _slots_from(source, i + len("from"), _join_split)
        i = _advance(source, i)
    return []


def _group_terms(node):
    """A GROUP BY's terms in the order grouporder.py renders them: the plain
    expressions, then GROUPING SETS / CUBE / ROLLUP."""
    group = node.args.get("group")
    if group is None or group.args.get("all"):
        return []
    terms = list(group.expressions)
    for key in ("grouping_sets", "cube", "rollup"):
        terms += list(group.args.get(key) or [])
    return terms


def _order_terms(node):
    order = node.args.get("order")
    return list(order.expressions) if order is not None else []


def _comma_clause_slots(source: str, keyword: str) -> list[tuple[int, int]]:
    """Slots for a two-word clause keyword (`GROUP BY` / `ORDER BY`), split on
    depth-0 commas."""
    head, tail = keyword.split()
    i = _skip_ws_comments(source, 0)
    n = len(source)
    while i < n:
        if (_IDENT_CHARS.match(source[i]) and _word_at(source, i, head)
                and (i == 0 or not _IDENT_CHARS.match(source[i - 1]))):
            j = _skip_ws_comments(source, i + len(head))
            if _word_at(source, j, tail):
                return _slots_from(source, j + len(tail), _comma_split)
        i = _advance(source, i)
    return []


def _group_of(c: Comment, groups):
    """The group whose slots contain `c`, or `(None, None, None)`."""
    for slots, targets, label in groups:
        if _slot_of(c, slots) is not None:
            return slots, targets, label
    return None, None, None


def _select_list_slots(source: str) -> list[tuple[int, int]]:
    """Partition the SELECT list into (start, end) source ranges, one per item.

    Scans past the leading `SELECT` keyword, then walks to the first depth-0
    clause keyword (or `;`/end), splitting on depth-0 commas. String/quoted-id/
    comment/dollar-quote-aware so a comma inside a literal or nested call is not
    a boundary.
    """
    i = _skip_ws_comments(source, 0)
    if not _word_at(source, i, "select"):
        return []
    return _slots_from(source, i + len("select"), _comma_split)


def _where_slots(source: str, keyword: str) -> list[tuple[int, int]]:
    """The same partition for a `WHERE`/`HAVING`/`QUALIFY` clause, split on its
    depth-0 `AND`/`OR` boundaries instead of on commas.

    Those are exactly the boundaries `condition_block` lays out one row per, so
    a comment lands on the row the author wrote it against: the whole reason
    the select list could be modelled at all.
    """
    i = _skip_ws_comments(source, 0)
    n = len(source)
    while i < n:
        if _IDENT_CHARS.match(source[i]) and _word_at(source, i, keyword) and (
                i == 0 or not _IDENT_CHARS.match(source[i - 1])):
            return _slots_from(source, i + len(keyword), _boolean_split)
        i = _advance(source, i)
    return []


# The words that may precede JOIN in a join clause. A comment written before
# `LEFT JOIN` belongs to the row ABOVE it, so the slot must break at `LEFT`,
# not at `JOIN`: otherwise the modifiers land in the previous slot and the
# comment lands on the wrong row.
_JOIN_MODIFIERS = frozenset({"natural", "cross", "inner", "outer",
                             "left", "right", "full", "lateral", "positional"})


def _join_split(source: str, i: int, depth: int) -> int:
    """The width of a FROM-list separator at `i`: a comma, or the start of a
    join clause (its first modifier word, or `JOIN` itself)."""
    if depth:
        return 0
    if source[i] == ",":
        return 1
    if not _IDENT_CHARS.match(source[i]) or (i and _IDENT_CHARS.match(source[i - 1])):
        return 0
    j, words = i, 0
    while j < len(source):
        k = j
        while k < len(source) and _IDENT_CHARS.match(source[k]):
            k += 1
        word = source[j:k].lower()
        if word == "join":
            return k - i                       # consume through JOIN itself
        if word not in _JOIN_MODIFIERS or words > 3:
            return 0
        words += 1
        j = _skip_ws_comments(source, k)
        if j == k:                             # no separator after the word
            return 0
    return 0


def _comma_split(source: str, i: int, depth: int) -> int:
    """Length of the separator at `i`, or 0. Commas separate select items."""
    return 1 if depth == 0 and source[i] == "," else 0


def _boolean_split(source: str, i: int, depth: int) -> int:
    """`AND` / `OR` at depth 0, as whole words."""
    if depth or not _IDENT_CHARS.match(source[i]):
        return 0
    if i and _IDENT_CHARS.match(source[i - 1]):
        return 0
    for word in ("and", "or"):
        if _word_at(source, i, word):
            return len(word)
    return 0


def _advance(source: str, i: int) -> int:
    """Step past whatever token starts at `i`, skipping over strings, quoted
    identifiers, comments and dollar-quoted bodies as single units."""
    ch = source[i]
    if ch == "'":
        return _skip_string(source, i)
    if ch == '"':
        return source.find('"', i + 1) + 1 or len(source)
    if source.startswith("--", i):
        nl = source.find("\n", i)
        return len(source) if nl == -1 else nl
    if source.startswith("/*", i):
        return _skip_block(source, i)
    if ch == "$" and (m := _DOLLAR.match(source, i)):
        return _skip_dollar(source, m)
    return i + 1


def _slots_from(source, list_start, separator):
    """Split `source[list_start:]` on `separator` until the first depth-0 clause
    keyword (or `;`/end), returning one (start, end) range per slot."""
    n = len(source)
    i = list_start
    depth = 0
    cuts: list[tuple[int, int]] = []          # (separator start, separator end)
    list_end = n
    while i < n:
        ch = source[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0 and (ch == ";" or (_IDENT_CHARS.match(ch)
                                         and _clause_word_at(source, i))):
            list_end = i
            break
        width = separator(source, i, depth)
        if width:
            cuts.append((i, i + width))
            i += width
            continue
        nxt = _advance(source, i)
        i = nxt if nxt > i else i + 1

    bounds = [list_start, *(end for _, end in cuts)]
    ends = [*(start for start, _ in cuts), list_end]
    return list(zip(bounds, ends, strict=False))


def _clause_word_at(source: str, i: int) -> bool:
    """True iff a `_CLAUSE_KEYWORDS` word begins at `i` (whole-word, and `i` is
    itself a word start: the caller only reaches here at a fresh identifier
    char, but guard the left boundary too)."""
    if i > 0 and _IDENT_CHARS.match(source[i - 1]):
        return False
    j = i
    while j < len(source) and _IDENT_CHARS.match(source[j]):
        j += 1
    return source[i:j].lower() in _CLAUSE_KEYWORDS


def _word_at(source: str, i: int, word: str) -> bool:
    seg = source[i:i + len(word)]
    if seg.lower() != word:
        return False
    after = i + len(word)
    return after >= len(source) or not _IDENT_CHARS.match(source[after])


def _slot_of(c: Comment, slots: list[tuple[int, int]]) -> int | None:
    for idx, (s, e) in enumerate(slots):
        if s <= c.start < e:
            return idx
    return None


def _only_trivia(source: str, lo: int, hi: int, comments: list[Comment]) -> bool:
    """True iff `source[lo:hi]` is only whitespace and (other) comment spans."""
    i = lo
    while i < hi:
        inside = next((c for c in comments if c.start <= i < c.end), None)
        if inside is not None:
            i = inside.end
            continue
        if not source[i].isspace():
            return False
        i += 1
    return True


def _skip_ws_comments(source: str, i: int) -> int:
    n = len(source)
    while i < n:
        if source[i] in " \t\r\n":
            i += 1
        elif source.startswith("--", i):
            nl = source.find("\n", i)
            i = n if nl == -1 else nl + 1
        elif source.startswith("/*", i):
            i = _skip_block(source, i)
        else:
            break
    return i


def _skip_string(source: str, i: int) -> int:
    """Offset one past the string literal opening at `i`.

    Handles both `''` escaping and, for an E-prefixed string, backslash escapes.
    """
    n = len(source)
    prev = source[i - 1] if i > 0 else ""
    prev2 = source[i - 2] if i > 1 else ""
    is_estring = prev in "eE" and not _IDENT_CHARS.match(prev2)
    i += 1
    while i < n:
        if is_estring and source[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if source[i] == "'" and not (i + 1 < n and source[i + 1] == "'"):
            break
        i += 2 if source[i] == "'" else 1
    return i + 1


def _skip_block(source: str, i: int) -> int:
    """Offset one past the `*/` closing the block comment at `i` (nesting-aware)."""
    n = len(source)
    depth, i = 1, i + 2
    while i < n and depth:
        if source.startswith("/*", i):
            depth, i = depth + 1, i + 2
        elif source.startswith("*/", i):
            depth, i = depth - 1, i + 2
        else:
            i += 1
    return i


def _skip_dollar(source: str, m: re.Match) -> int:
    """Offset one past the dollar-quoted body opened by the `$tag$` match `m`."""
    tag = m.group(0)
    end = source.find(tag, m.end())
    return len(source) if end == -1 else end + len(tag)
