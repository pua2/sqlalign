"""Window-function layout: a select-list
item whose value is `exp.Window`: `FUNC(...) OVER (PARTITION BY ...
[ORDER BY ...] [frame])`. `OVER` gets a space before its paren (a keyword,
not a function name -- same family as `IN (...)`/`EXISTS (...)`; spec's
deliberate change from the raw samples' `OVER(`).

Geometry is precomputed, not resolver-driven: the same simplification
subquery.py's module docstring documents for a recursed body's baked-in
indent: `content_col` (where PARTITION/ORDER BY/the frame clause sit, and
where every continuation line aligns) is plain arithmetic off the function
call's own rendered length (`item_col + len(FUNC(...) OVER () )`), because
every segment feeding that arithmetic: the function call text, the literal
`" OVER ("` -- is itself untagged, so its natural column IS its rendered
column and can never be shifted by a later resolver pass. Verified
empirically against fixture #9 (pinned sqlglot v30.14): this formula lands
`PARTITION` at column 26/24/29 for the sample's three windows (inline,
frame-dropped, and inline again respectively) -- exactly the fixture's
columns, whether or not that window ends up breaking.

Breaking ladder (spec): (1) inline if `FUNC(...) OVER (...)` fits the width
limit; (2) else, break inside the parens at sub-clause boundaries, peeling
from the END of the fixed [PARTITION BY, ORDER BY, frame] priority order one
sub-clause at a time: the frame clause drops to its own line FIRST, and
only if PARTITION BY + ORDER BY still don't fit combined does ORDER BY also
get its own line; (3) if a single sub-clause's own column list is still too
long even alone on its line, it stacks like a GROUP BY (leading commas) --
this last rung is untested by any fixture (#9's sub-clauses are all short)
but is implemented as a defensive extension of the same ladder, documented
where it lives (`_stack_subclause`). The window always closes inline (`)`
glued onto the last line, like scalar/IN/EXISTS subqueries: ) and
the `AS` alias participates in the caller's (select.py's) own select-list
alignment scope, exactly like any other item.

`exp.WindowSpec`'s `kind`/`start_side`/`end_side` args hold RAW,
source-preserved-casing text (verified empirically; this is exactly why
formatter.py's `_CASEFOLD_STR_ARGS` table already lists them for
`ast_equal`'s casefold pass) -- `render_expr` on a bare spec node does NOT
uppercase them the way it uppercases function names, so `_frame_text` below
manually upper-cases those three fields on a COPY of the spec node before
rendering (house style: keywords upper, never touching a genuine string
literal: these three fields are never user data, only fixed keyword
vocabulary, so this is safe).
"""
from sqlglot import exp

from sqlalign.casing import active_style, render_expr
from sqlalign.ir import Line, Seg
from sqlalign.layout import Unsupported, comma_clause, select_item_col

_PREFIX_TAIL = " OVER ("       # space-before-paren: OVER is a keyword, not a function name


def window_lines(win_node: exp.Window, anchor: int, dialect: str, width) -> list[Line]:
    """Lay out `win_node` (a select item's window-function value) at
    select-statement anchor `anchor`. Line 0's `indent` is `anchor` itself
    (not the item column) so the caller (select.py) can splice its own
    lead-in segment onto the front, exactly like case.py's `case_lines`.
    """
    _guard_window(win_node)
    # `SUM(x) OVER w`: a REFERENCE to a window named in the WINDOW clause.
    # There is no spec to lay out here; the spec lives in that clause, and the
    # whole item is one short line whatever the width.
    named = win_node.args.get("alias")
    if named is not None:
        func = render_expr(win_node.this, dialect)
        return [Line(anchor, [Seg(f"{func} OVER {render_expr(named, dialect)}")])]

    item_col = select_item_col(anchor)
    func_text = render_expr(win_node.this, dialect)
    prefix = func_text + _PREFIX_TAIL
    content_col = item_col + len(prefix)

    partition = _partition_text(win_node, dialect)
    order = _order_text(win_node, dialect)
    frame = _frame_text(win_node, dialect)
    subclauses = [s for s in (partition, order, frame) if s is not None]
    if not subclauses:
        # `COUNT(*) OVER ()`: the empty window, which means "the whole result
        # set". It is ordinary SQL and there is nothing to lay out: an empty
        # pair of parens on the function's own line.
        return [Line(anchor, [Seg(prefix + ")")])]

    limit = width.limit(item_col)
    full = prefix + " ".join(subclauses) + ")"
    if item_col + len(full) <= limit:
        return [Line(anchor, [Seg(full)])]

    rows = _break_rows(subclauses, content_col, limit)
    lines = [Line(anchor, [Seg(prefix + rows[0])])]
    lines += [Line(content_col, [Seg(r)]) for r in rows[1:]]
    lines[-1].segs[-1].text += ")"
    return lines


def named_window_lines(windows, anchor: int, dialect: str) -> list[Line]:
    """The `WINDOW w AS (...)` clause: a named spec reused by `OVER w`.

    Composed from the same three spec builders `window_lines` uses, rather than
    from `render_expr`, which prints a named window as `w OVER (...)`: the
    inline spelling. The clause form needs `AS`, and rewriting one into the other
    with string surgery would be a guess about a shape sqlglot is free to change.
    """
    terms = []
    for win in windows:
        if win.comments:
            raise Unsupported("embedded comment")
        name = win.this
        if name is None or win.args.get("alias"):
            raise Unsupported("WINDOW clause: unexpected shape")
        parts = [p for p in (_partition_text(win, dialect), _order_text(win, dialect),
                             _frame_text(win, dialect)) if p]
        terms.append(f"{render_expr(name, dialect)} AS ({' '.join(parts)})")
    return comma_clause("WINDOW", terms, anchor)


def _guard_window(win: exp.Window) -> None:
    """Decline (`Unsupported`) window shapes this module does not model.
    Verified empirically (pinned sqlglot v30.14, `exp.Window.arg_types`):
    `alias` only populates for a named `WINDOW w AS (...)` clause (that
    clause itself is already gated -- select.py's `_ALLOWED_ARGS` excludes
    the Select `windows` arg entirely); `first` carries a FIRST/LAST
    positional flag (e.g. `IGNORE NULLS` ordering) sample 09 never
    exercises.
    """
    if win.comments:
        raise Unsupported("embedded comment")
    if win.args.get("alias") and any(
            win.args.get(k) for k in ("partition_by", "order", "spec")):
        # A reference carries only the name; a name PLUS an inline spec is
        # `OVER w (ORDER BY ...)`, a shape this layer does not model.
        raise Unsupported("window: named reference with an inline spec")
    if win.args.get("first") is not None:
        raise Unsupported("window: FIRST/LAST positional flag")


def _partition_text(win: exp.Window, dialect: str) -> str | None:
    cols = win.args.get("partition_by")
    if not cols:
        return None
    return "PARTITION BY " + ", ".join(render_expr(c, dialect) for c in cols)


def _order_text(win: exp.Window, dialect: str) -> str | None:
    order = win.args.get("order")
    if order is None:
        return None
    if order.args.get("this") is not None or order.args.get("siblings"):
        raise Unsupported("window ORDER BY: exotic form")
    return "ORDER BY " + ", ".join(render_expr(e, dialect) for e in order.expressions)


def _frame_text(win: exp.Window, dialect: str) -> str | None:
    spec = win.args.get("spec")
    if spec is None:
        return None
    spec = spec.copy()
    for key in ("kind", "start_side", "end_side"):
        val = spec.args.get(key)
        if isinstance(val, str):
            spec.set(key, val.upper())
    return render_expr(spec, dialect)


def _break_rows(subclauses: list[str], content_col: int, limit: int) -> list[str]:
    """Peel sub-clauses off the END of the fixed [PARTITION BY, ORDER BY,
    frame] priority order, one at a time, until the first (leftmost)
    combined row fits: the frame clause always peels before ORDER BY, per
    spec. Each resulting row is then individually checked for its own
    comma-stacking need (`_stack_subclause`): a no-op for any row that
    already fits, which every row with `k > 1` sub-clauses combined does by
    construction (the peel loop only stops once the combination fits).
    """
    k = len(subclauses)
    while k > 1:
        head = " ".join(subclauses[:k])
        if content_col + len(head) <= limit:
            break
        k -= 1
    grouped = [" ".join(subclauses[:k]), *subclauses[k:]]
    rows = []
    for g in grouped:
        rows.extend(_stack_subclause(g, content_col, limit))
    return rows


def _stack_subclause(text: str, content_col: int, limit: int) -> list[str]:
    """If a single sub-clause (e.g. `"PARTITION BY a, b, c"`) still exceeds
    the width alone on its own row, stack it like a GROUP BY:
    keyword + first term open the row, later terms one per line with a
    leading comma two columns before the term. Untested by any fixture
    (#9's sub-clauses are all short) -- a defensive extension of the
    ladder's last rung, not a golden-verified shape.
    """
    if content_col + len(text) <= limit:
        return [text]
    keyword, sep, rest = text.partition(" BY ")
    if not sep:
        return [text]                          # not a "KEYWORD BY a, b, ..." shape -- leave as-is
    keyword += " BY"
    terms = [t.strip() for t in rest.split(",")]
    lead = " " * (len(keyword) - 1)
    # This site builds finished text rather than Segs, so it applies the comma
    # position itself instead of going through commas.py's IR pass.
    if active_style().comma_position == "trailing":
        rows = [f"{keyword} {terms[0]}"] + [f"{lead} {t}" for t in terms[1:]]
        return [r + "," for r in rows[:-1]] + [rows[-1]]
    return [f"{keyword} {terms[0]}"] + [f"{lead}, {t}" for t in terms[1:]]
