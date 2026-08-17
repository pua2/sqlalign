"""WHERE / HAVING / ON / QUALIFY geometry.

A boolean predicate is flattened at its top level into one condition per line.
The clause keyword and the AND/OR joiners form a right-aligned family (all end at
the same column); the operator of each condition is tagged `kind="op"` so the
resolver right-aligns operators too. Parenthesized groups recurse under a child
scope so their operator column is independent of the parent's.

Column model (all columns absolute, 0-based):
  key_col the block keyword starts here (== Line.indent for every row)
  body_col  = key_col + max_kw + 1, where max_kw is the widest keyword in the
            family (clause keyword and joiners). Each condition body begins here,
            so the keyword occupies [key_col, body_col-2] and one space follows.
A group hangs its first condition directly off "(" (that condition begins at the
group's key_col, hugging the paren) while later conditions get the joiner + body.

`IN (SELECT ...)` and `EXISTS (SELECT ...)` are two more shapes
`_render_condition` special-cases before falling through to `predicate_segs`:
like a group, both recurse a nested body off `body_col`/`key_col` and can
produce `trailing_lines` — but via subquery.py's `layout_statement` recursion
(the subquery's own `SELECT`/FROM/WHERE), not another flattened conjunction —
and both close inline, unlike a group's own `)`. Unlike every
other operator in this module, `IN`'s keyword in this shape is deliberately
UNTAGGED (no scope/kind): excluded from the operator right-alignment family
— matching `EXISTS`, which was always untagged. See `_render_in_subquery`'s
docstring and subquery.py's module docstring for why: the recursed body's
line indents are baked in from `IN`'s NATURAL column, so a resolver-padded
`IN` would silently detach the body from its own `(`. Plain `IN (list)`
(`predicate_segs`, below) is a different code path and still right-aligns.
"""
import functools

import sqlglot
from sqlglot import exp

from sqlalign.casing import active_style, render_expr
from sqlalign.ir import Line, Seg, comment_seg, split_trailing_comments
from sqlalign.layout import Unsupported

# Binary comparison / pattern operators whose left and right operands both render
# cleanly via render_expr; the value here is the operator token laid out as the
# right-aligned `op` segment.
# NEQ canonicalizes to "!=" (the house form): sqlglot erases the "!=" vs "<>"
# source spelling at parse (both -> exp.NEQ), so "kept as written" is impossible
# through the AST; "!=" is what every in-house sample uses. Zero semantic
# difference, so this stays ast_equal-safe.
_CMP_OP = {
    exp.EQ: "=", exp.NEQ: "!=", exp.GT: ">", exp.GTE: ">=",
    exp.LT: "<", exp.LTE: "<=", exp.Like: "LIKE", exp.ILike: "ILIKE",
    # The null-safe pair. They are comparison operators like any other: the
    # only reason they declined is that they were missing from this table, and
    # `IS DISTINCT FROM` is the standard way to compare nullable columns.
    exp.NullSafeNEQ: "IS DISTINCT FROM", exp.NullSafeEQ: "IS NOT DISTINCT FROM",
    # Pattern matching beyond LIKE. `!~` / `!~*` are `Not` wrapping these two,
    # handled below: sqlglot prints those as `NOT b ~ 'x'`, but the author
    # wrote an operator and it belongs in the operator column.
    exp.SimilarTo: "SIMILAR TO", exp.RegexpLike: "~", exp.RegexpILike: "~*",
}
_NEGATED_OP = {exp.RegexpLike: "!~", exp.RegexpILike: "!~*"}


def condition_block(keyword, node, scope, anchor, dialect, width):
    """Lay out the predicate `node` introduced by `keyword` at column `anchor`."""
    conditions, joiner = split_conjunction(node)
    keywords = [keyword] + ([joiner] * (len(conditions) - 1) if joiner else [])
    max_kw = max(len(k) for k in keywords)
    style = active_style()
    # In a river the whole keyword family right-aligns to the gutter rather than
    # to its own widest member, so WHERE/AND land in the same column FROM and
    # SELECT do. A JOIN's ON is not a root clause and keeps its local geometry.
    if style.clause_keyword_align == "river" and keyword in ("WHERE", "HAVING"):
        max_kw = max(max_kw, style.river_gutter)
    # `body_col` is identical under either boolean position: only the AND/OR
    # moves; see `joiner_head`.
    body_col = anchor + max_kw + 1
    group_counter = [0]
    lines = []
    for i, (kw, cond) in enumerate(zip(keywords, conditions, strict=False)):
        segs, extra = _render_condition(cond, scope, body_col, group_counter, dialect, width)
        # Comments the engine attached to this condition (comments.py). Same
        # placement rule the select list uses, and for the same reason: a `--`
        # or multi-line comment cannot share a row with content that follows it,
        # so it takes the END of the row above, which is also where the author
        # wrote it, and the only idempotent spelling.
        lead = cond.meta.get("sqlalign_lead")
        if lead is not None and ("\n" in lead or lead.startswith("--")):
            if lines:
                lines[-1].segs.append(comment_seg(lead))
            else:
                lines.append(Line(anchor, [comment_seg(lead)]))
            lead = None
        head = Seg(kw.rjust(max_kw)) if i == 0 else joiner_head(kw, max_kw, lines[-1].segs)
        row = [head, *([comment_seg(lead)] if lead is not None else []), *segs]
        lines.append(Line(anchor, row))
        lines.extend(extra)
        trail = cond.meta.get("sqlalign_trail")
        if trail is not None:
            lines[-1].segs.append(comment_seg(trail))
    return lines


def joiner_head(joiner, max_kw, prev_segs):
    """The head segment of a continuation (AND/OR) row, right-justified to
    `max_kw` so every keyword in the family ends at the same column.

    With trailing booleans the joiner instead rides the END of the preceding
    row (`prev_segs`, appended to in place) and the head becomes blank of the
    same width, so the condition column is identical in both modes: only the
    operator moves. `prev_segs` must be the segs of whatever row ACTUALLY
    precedes this one, never the block's keyword row: a condition that itself
    spans rows (a group, an `IN (SELECT ...)`) leaves its own last row on top,
    and that is where the joiner belongs.

    Public: also used by case.py, whose long-form WHEN/AND/OR family is this
    same geometry with "WHEN" standing in for the clause keyword.
    """
    if active_style().boolean_operator_position == "trailing":
        # Before any trailing comment, never after it: a `--` runs to end of
        # line, so a joiner appended past one is INSIDE it and the condition
        # below loses its AND/OR entirely.
        content, comments = split_trailing_comments(prev_segs)
        content[-1].text += " " + joiner
        prev_segs[:] = [*content, *comments]
        return Seg(" " * max_kw)
    return Seg(joiner.rjust(max_kw))


def _render_condition(cond, scope, body_col, group_counter, dialect, width):
    """Return (inline_segs, trailing_lines) for one condition.

    `inline_segs` are placed on the current row (after this block's keyword);
    `trailing_lines` are any additional full rows (groups, and
    `IN (SELECT ...)` / `EXISTS (SELECT ...)`, produce them). `body_col` is the
    absolute column the inline content will start at — used to place the "("
    of a nested group, or of an IN/EXISTS subquery's body (subquery.py).
    """
    if isinstance(cond, exp.Paren):
        return _render_group(cond, scope, body_col, group_counter, dialect, width)
    if isinstance(cond, exp.In) and cond.args.get("query") is not None:
        return _render_in_subquery(cond, body_col, dialect, width)
    if isinstance(cond, exp.Exists):
        return _render_exists(cond, body_col, dialect, width)
    # The subquery forms of the same negation. They are peeled HERE rather than
    # in `predicate_segs` because both emit trailing lines whose indents are
    # baked in from the natural column, so `NOT ` has to widen `body_col` by
    # its own length or the body detaches from its `(`.
    if isinstance(cond, exp.Not) and isinstance(cond.this, exp.Exists):
        segs, trailing = _render_exists(cond.this, body_col + len("NOT "), dialect, width)
        return [Seg("NOT"), *segs], trailing
    if (isinstance(cond, exp.Not) and isinstance(cond.this, exp.In)
            and cond.this.args.get("query") is not None):
        # `x NOT IN (SELECT ...)`, not `NOT x IN (SELECT ...)`: both are valid
        # and mean the same thing, but only one is how anyone writes it. not
        # goes on the keyword, which is also what keeps the body under its own
        # paren, since the keyword's own width feeds `open_col`.
        return _render_in_subquery(cond.this, body_col, dialect, width, negation="NOT ")
    return predicate_segs(cond, scope, dialect), []


def _render_in_subquery(cond, body_col, dialect, width, negation=""):
    """`lhs IN (SELECT ...)`. `body_col` is where
    `lhs` itself starts; the subquery's body recurses one column past its own
    "(", which — per the natural (unpadded) flow every segment here composes
    under, see subquery.py's module docstring — sits at
    `body_col + len(lhs) + 1 (space) + len("IN") + 1 (space)`.

    The `IN` keyword is deliberately emitted UNTAGGED (no scope/kind), unlike
    every other WHERE/HAVING operator: the derived-table exclusion
    analogue. If `IN` were tagged into the shared `op` scope, a sibling
    condition with a longer LHS would pad it rightward at render time, but
    the subquery body's line indents are baked in here from the NATURAL
    (unpadded) open-paren column (see subquery.py's module docstring on why
    that's unavoidable) — so a padded `IN` would silently detach the body
    from its own `(`. Leaving `IN` untagged keeps its natural column equal to
    its rendered column, so the two can never diverge. Plain `IN (list)`
    (`predicate_segs`, above) is unaffected and still right-aligns.
    """
    from sqlalign.layout.subquery import guard_subquery, subquery_body

    if any(cond.args.get(k) for k in ("unnest", "field")):
        raise Unsupported("IN (non-list, non-query)")
    subquery = cond.args["query"]
    guard_subquery(subquery, allow_alias=False)
    lhs = render_expr(cond.this, dialect)
    keyword = negation + "IN"
    lead = [Seg(lhs), Seg(keyword)]
    open_col = body_col + len(lhs) + 1 + len(keyword) + 1
    open_segs, trailing = subquery_body(subquery.this, dialect, width, open_col + 1)
    return lead + open_segs, trailing


def _render_exists(cond, body_col, dialect, width):
    """`EXISTS (SELECT ...)`. Verified empirically
    (pinned sqlglot v30.14): `exp.Exists.this` is the bare inner `Select` --
    unlike `IN`'s `query` arg, there is no `exp.Subquery` wrapper to guard.
    """
    from sqlalign.layout.subquery import subquery_body

    if cond.args.get("expression") is not None:
        raise Unsupported("EXISTS: expression arg")
    open_col = body_col + len("EXISTS") + 1
    open_segs, trailing = subquery_body(cond.this, dialect, width, open_col + 1)
    return [Seg("EXISTS"), *open_segs], trailing


def _render_group(paren, parent_scope, open_col, group_counter, dialect, width):
    """Lay out a parenthesized group opening at column `open_col`.

    The group's operators get their own child scope so they align independently
    of the enclosing block.
    """
    scope = f"{parent_scope}.g{group_counter[0]}"
    group_counter[0] += 1
    conditions, joiner = split_conjunction(paren.this)
    key_col = open_col + 1                       # first char after "("
    keywords = [""] + ([joiner] * (len(conditions) - 1) if joiner else [])
    max_kw = max(len(k) for k in keywords)
    body_col = key_col + max_kw + 1

    seg0, trail0 = _render_condition(conditions[0], scope, key_col, group_counter, dialect, width)
    inline = [_prefixed(seg0[0], "("), *seg0[1:]]     # first condition hugs "("
    lines = list(trail0)
    for cond in conditions[1:]:
        segs, trailing = _render_condition(cond, scope, body_col, group_counter, dialect, width)
        # The group's first condition lives INLINE on the caller's row, so the
        # boolean for the first continuation belongs on that row: hence
        # `inline` when no rows of our own exist yet.
        head = joiner_head(joiner, max_kw, lines[-1].segs if lines else inline)
        lines.append(Line(key_col, [head, *segs]))
        lines.extend(trailing)

    if lines:
        lines[-1].segs[-1].text += ")"
    else:
        inline[-1].text += ")"
    return inline, lines


@functools.cache
def _keeps_negated_is_apart(dialect: str) -> bool:
    """Whether `dialect` still distinguishes `x IS NOT NULL` from `NOT x IS NULL`.

    Postgres parses the first as `Is(negate=True)` and the second as
    `Not(Is(...))`, so a `Not(Is(...))` is the author's `NOT ... IS NULL`, and
    printing it as `IS NOT NULL` rewrites SQL they did not write. T-SQL and
    Redshift collapse both spellings onto `Not(Is(...))`; the distinction is gone
    before the layout sees it, and the idiomatic spelling is then the only
    sensible choice.

    The probe uses the NULL spelling and its answer applies ONLY to a NULL (or
    UNKNOWN, which sqlglot folds to NULL) right-hand side. The boolean forms
    collapse even in Postgres -- `x IS NOT TRUE` and `NOT x IS TRUE` are one
    tree -- so for those the idiomatic spelling is the only sensible choice in
    every dialect, and treating them like NULL rewrote `x IS NOT TRUE` into
    `NOT x IS TRUE` with nothing able to notice.

    Probed rather than listed against dialect names, so a parser change upstream
    is followed instead of silently disagreed with.
    """
    where = sqlglot.parse_one("SELECT 1 FROM t WHERE x IS NOT NULL",
                              dialect=dialect).args["where"].this
    return isinstance(where, exp.Is)


def predicate_segs(cond, scope, dialect):
    """Split one predicate into [lhs, op(tagged), rhs] segments.

    Public: also used by fromjoin.py to render ON conditions, which share this
    module's operator-comparison vocabulary (=, !=, BETWEEN, IN, IS, ...) but lay
    out their keyword/joiner column very differently (block-global, alias-driven
    rather than `condition_block`'s anchor-relative keyword rjust).
    """
    if isinstance(cond, exp.Not) and isinstance(cond.this, exp.Is):
        # `x IS NOT NULL` is one shape in postgres (Is with negate=True, handled
        # below) and ANOTHER in T-SQL, which wraps it: Not(Is(...)). Without this
        # the single most common predicate in SQL declined for T-SQL only.
        # Deliberately narrow: only a Not directly wrapping an Is. Every other
        # Not stays declined, which `test_not_exists_passes_through` depends on.
        inner = cond.this
        inner_negated = bool(inner.args.get("negate"))
        if _keeps_negated_is_apart(dialect) and isinstance(inner.expression, exp.Null):
            # Here -- and only here -- the tree still says which spelling the
            # author wrote: `x IS NOT NULL` parses to Is(negate), so a Not
            # wrapping an Is is their `NOT ... IS [NOT] NULL` and is preserved.
            # The inner negate rides along; dropping it turned
            # `NOT x IS NOT NULL` into its logical inverse, which only the
            # re-parse guard stopped from shipping.
            rhs = ("NOT " if inner_negated else "") + render_expr(inner.expression, dialect)
            return [Seg("NOT " + render_expr(inner.this, dialect)),
                    Seg("IS", scope=scope, kind="op"),
                    Seg(rhs)]
        if not inner_negated:
            # One tree serves both spellings here (T-SQL and Redshift for NULL,
            # every dialect for the boolean forms), so which comes out is
            # sqlalign's to pick, and `IS NOT x` is the idiomatic pick.
            lhs = render_expr(inner.this, dialect)
            rhs = "NOT " + render_expr(inner.expression, dialect)
            return [Seg(lhs), Seg("IS", scope=scope, kind="op"), Seg(rhs)]
        # Not(Is(negate)) in a dialect that collapses the spellings has no
        # faithful single-IS rendering; fall through to the decline paths.

    # `NOT IN (...)` and `NOT BETWEEN ... AND ...`: sqlglot wraps the inner
    # predicate in a `Not`, so peeling it here puts NOT on the operator, where a
    # reader looks for it and where the aligned operator column holds it.
    negation = ""
    if isinstance(cond, exp.Not) and isinstance(cond.this, (exp.In, exp.Between)):
        cond, negation = cond.this, "NOT "

    # `b !~ 'x'` is its own operator, not a negated one, so it keeps the
    # operator column to itself rather than growing a `NOT ` prefix.
    if isinstance(cond, exp.Not) and type(cond.this) in _NEGATED_OP:
        inner = cond.this
        return [Seg(render_expr(inner.this, dialect)),
                Seg(_NEGATED_OP[type(inner)], scope=scope, kind="op"),
                Seg(render_expr(inner.expression, dialect))]

    if isinstance(cond, exp.Between):
        # `BETWEEN SYMMETRIC 2 AND 1` means the same as `BETWEEN 1 AND 2` --
        # the bounds may be given in either order. It is one keyword on the
        # operator, and dropping it would change the meaning, which is why it
        # declined rather than being ignored.
        symmetric = " SYMMETRIC" if cond.args.get("symmetric") else ""
        lhs = render_expr(cond.this, dialect)
        low = render_expr(cond.args["low"], dialect)
        high = render_expr(cond.args["high"], dialect)
        return [Seg(lhs), Seg(negation + "BETWEEN" + symmetric, scope=scope,
                              kind="op"),
                Seg(f"{low} AND {high}")]

    if isinstance(cond, exp.In):
        if any(cond.args.get(k) for k in ("query", "unnest", "field")):
            raise Unsupported("IN (non-list)")
        lhs = render_expr(cond.this, dialect)
        vals = ", ".join(render_expr(e, dialect) for e in cond.args.get("expressions", []))
        return [Seg(lhs), Seg(negation + "IN", scope=scope, kind="op"), Seg(f"({vals})")]

    if isinstance(cond, exp.Is):
        lhs = render_expr(cond.this, dialect)
        rhs = ("NOT " if cond.args.get("negate") else "") + render_expr(cond.expression, dialect)
        return [Seg(lhs), Seg("IS", scope=scope, kind="op"), Seg(rhs)]

    if isinstance(cond, exp.Escape):
        # `b LIKE 'x%' ESCAPE '!'` parses as Escape(Like(...)). The escape rides
        # the right-hand segment rather than taking the operator column: it
        # qualifies the pattern, and in the operator column it would align
        # `ESCAPE` under a bare `=`.
        inner = predicate_segs(cond.this, scope, dialect)
        escape = render_expr(cond.expression, dialect)
        return [*inner[:-1], Seg(f"{inner[-1].text} ESCAPE {escape}")]

    op = _CMP_OP.get(type(cond))
    if op is None:
        # A predicate with no comparison operator to hold a column: a bare
        # boolean column (`WHERE flag`), a literal (`WHERE false`), a call
        # returning boolean (`WHERE my_check(a)`), a negated group
        # (`WHERE NOT (a AND b)`). There is nothing to tag, so it is one
        # untagged segment that does not participate in the operator column --
        # the same treatment an unaliased table gets in the alias column.
        return [Seg(negation + render_expr(cond, dialect))]
    if isinstance(cond, exp.NEQ):
        op = active_style().neq_style      # `!=` (house) or `<>` -- see casing.py
    if isinstance(cond, (exp.Like, exp.ILike)) and cond.args.get("negate"):
        # `x NOT LIKE 'a'` is one node carrying a flag, not a wrapped `Not`.
        op = "NOT " + op
    lhs = render_expr(cond.this, dialect)
    rhs = render_expr(cond.expression, dialect)
    return [Seg(lhs), Seg(op, scope=scope, kind="op"), Seg(rhs)]


def split_conjunction(node):
    """Flatten a top-level AND/OR chain into (conditions, joiner).

    Public: shared with fromjoin.py for flattening ON conditions.
    """
    if isinstance(node, exp.And):
        return _flatten(node, exp.And), "AND"
    if isinstance(node, exp.Or):
        return _flatten(node, exp.Or), "OR"
    return [node], None


def _flatten(node, cls):
    out = []
    for side in (node.left, node.right):
        out.extend(_flatten(side, cls) if isinstance(side, cls) else [side])
    return out


def _prefixed(seg, prefix):
    """A copy of `seg` with `prefix` glued to the front of its text."""
    return Seg(prefix + seg.text, scope=seg.scope, kind=seg.kind)
