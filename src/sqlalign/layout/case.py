"""CASE-expression layout: a select-list item whose
value is a CASE expression, in either spelling. Verified empirically (pinned
sqlglot v30.14): `exp.Case.arg_types = {this: False, ifs: True, default:
False}`; `this` is the simple-CASE operand (`CASE x WHEN v1 THEN r1 ...`) and
is `None` for a searched CASE, which is all sample 08 exercises.

The simple form needs no geometry of its own: the operand rides the CASE line,
pushing the WHEN column right by its width, and one rule then covers both
spellings: the first WHEN rides line 0, every later WHEN aligns under it. For a searched CASE that
column is `item_col + len("CASE ")`, exactly what it always was. In the simple
form `iff.this` is a VALUE to compare against rather than a predicate; it
renders the same way into the same column, which is why THEN alignment needed
no special case either.

Both forms below open with `CASE WHEN ...` on the item's own row and close
with `END` directly under `CASE`
(fixed at the item column -- not resolver-tagged, the same "END under CASE"
relationship short-form's THEN-vs-CASE column pairing establishes structurally
rather than through alignment).

Short form: every `WHEN cond THEN val` fits on one row: `WHEN`s align one
space after `CASE` (`item_col + len("CASE ")`); `THEN` is tagged
`kind="then"` in a per-CASE scope (`f"case@{id(case_node)}"`, id()-namespaced
per house convention so a sibling CASE item's THEN column never bleeds into
this one) so the resolver -- not manual padding -- pads every row's THEN to
the widest condition's column, exactly the mechanism the fixpoint resolver
resolver exists to compose.

Long form: the first WHEN rides the CASE line; every subsequent WHEN/ELSE
starts its own row at the WHEN column; THEN drops to ITS OWN row, indented 2
past WHEN (a fixed offset, not resolver-tagged -- every WHEN's THEN sits at
the identical `when_col + 2` regardless of condition length, unlike
short-form's content-dependent THEN column). A WHEN whose condition is
compound (AND/OR) spills its continuation operand(s) onto further rows with
the boolean joiner right-aligned under WHEN -- precisely conditions.py's
`condition_block` geometry with "WHEN" standing in for the clause keyword
(and "AND"/"OR" as the joiner: `max_kw = max(len("WHEN"), len(joiner))`,
right-justified, so a 3-char "AND"/2-char "OR" right-aligns to the same end
column "WHEN" occupies) -- reused directly below via `_when_condition_lines`
rather than re-deriving that arithmetic. Unlike `condition_block`'s own
keyword right-alignment (a fixed Python `.rjust()`, not a resolver tag --
see that module's docstring), the WHEN/AND/OR family here is likewise pure
Python arithmetic; only THEN (short form) and each condition's own comparison
operator (both forms, via `predicate_segs`'s `kind="op"` tag) go through the
resolver.

DISCREPANCY (fixture-driven; house convention is fixture bytes over
spec/brief wording: noted here per that convention): the spec and task
brief describe the short/long choice as purely width-driven ("short form:
fits in width"). Sample 08's second CASE (status_rank) contradicts a pure
width reading: every one of its WHEN...THEN rows would fit comfortably under
the width limit even laid out in short form (the longest, `WHEN status =
'pending' AND NOT is_archived THEN 2`, is 50 columns wide starting at column
12: nowhere near the ~105-column limit), yet the fixture uses long form
throughout, including its FIRST, non-compound WHEN (`status = 'complete'`).
The bytes-verified actual trigger is structural, not width-based: a CASE
goes long-form the instant ANY of its WHEN conditions is a compound (AND/OR)
boolean: forcing uniform long-form layout across every WHEN in that same
CASE, mirroring how a single compound WHERE condition forces per-operand
line layout regardless of length. A too-long *simple* (non-compound)
condition also still falls back to long form as a width safety valve --
untested by the fixtures, but keeps the short-form promise ("fits in
width") honest for that case, and it is the only place width genuinely
enters this module's short/long decision.

`NOT is_archived` (a bare boolean operand, not a binary comparison) appears
inside the second CASE's compound WHEN. `conditions.py`'s shared
`predicate_segs` deliberately has no `exp.Not` case and raises `Unsupported`
for it everywhere else in the codebase -- `test_subquery.py`'s
`test_not_exists_passes_through` depends on exactly that decline (`NOT
EXISTS (...)` in a WHERE clause must safely pass through, not silently drop
the negation), so this module does NOT loosen `predicate_segs` itself, which
would defeat that test and re-open the "silently render an unmodeled boolean
shape everywhere" risk `predicate_segs`'s narrow contract exists to close.
Instead `_when_condition_lines` below (this module's own, CASE-WHEN-only
condition renderer, structurally parallel to but independent of
`condition_block`) tries `predicate_segs` first and falls back to one plain
`render_expr`-rendered segment: ast_equal-safe by construction, since it is
the exact same node re-serialized faithfully: for anything `predicate_segs`
declines. The fallback is reachable ONLY from a CASE's WHEN, never from
WHERE/HAVING/ON, so `NOT EXISTS` and every other predicate shape
`predicate_segs` is deliberately narrow about stay exactly as narrow
everywhere else.
"""
from sqlglot import exp

from sqlalign.casing import render_expr
from sqlalign.ir import Line, Seg
from sqlalign.layout import Unsupported, select_item_col
from sqlalign.layout.conditions import joiner_head, predicate_segs, split_conjunction

_WHEN_OFFSET = len("CASE ")     # 5: WHEN sits one space after CASE, both forms


def case_lines(case_node: exp.Case, anchor: int, dialect: str, width) -> list[Line]:
    """Lay out `case_node` (a select item's CASE value) at select-statement
    anchor `anchor`. Line 0's `indent` is `anchor` itself (NOT the item
    column) so the caller (select.py) can splice its own lead-in segment
    (`SELECT` / the leading comma) onto the front of line 0's segs, exactly
    like every other select-list item -- render()'s automatic one-space
    inter-seg separator then lands `CASE` at the item column on its own,
    with no manual space-counting here.
    """
    ifs = case_node.args["ifs"]
    default = case_node.args.get("default")
    item_col = select_item_col(anchor)
    # The simple form's test operand rides the CASE line, so `head` grows and the
    # WHEN column moves right with it. One rule covers both forms: the first WHEN
    # rides line 0 and every later WHEN aligns under it, which for a searched
    # CASE is `item_col + len("CASE ")` exactly as before.
    operand = case_node.args.get("this")
    head = "CASE" if operand is None else f"CASE {render_expr(operand, dialect)}"
    when_col = item_col + len(head) + 1

    if _fits_short_form(ifs, when_col, item_col, dialect, width):
        return _short_form(case_node, ifs, default, anchor, item_col, when_col, dialect,
                           head)
    return long_form_case_lines(case_node, ifs, default, anchor, when_col, dialect,
                                head=head, end_indent=item_col)


def _fits_short_form(ifs, when_col, item_col, dialect, width) -> bool:
    """Short form's eligibility test (see module docstring's DISCREPANCY
    note): disqualified the instant any WHEN condition is compound (AND/OR)
    -- long form's boolean-operator alignment is the house treatment for
    that, unconditionally, the same way WHERE never keeps a compound
    condition on one packed line regardless of width. Absent any compound
    condition, short form additionally requires every `WHEN cond THEN val`
    row to fit the width limit on its own (untested by the fixtures, but
    keeps "short form: fits in width" a real constraint for a merely-long
    simple condition rather than a dead letter).
    """
    if any(isinstance(iff.this, (exp.And, exp.Or)) for iff in ifs):
        return False
    limit = width.limit(item_col)
    for iff in ifs:
        cond_text = render_expr(iff.this, dialect)
        val_text = render_expr(iff.args["true"], dialect)
        row_len = when_col + len("WHEN ") + len(cond_text) + len(" THEN ") + len(val_text)
        if row_len > limit:
            return False
    return True


def _short_form(case_node, ifs, default, anchor, item_col, when_col, dialect,
                head="CASE") -> list[Line]:
    scope = f"case@{id(case_node)}"
    lines = []
    for i, iff in enumerate(ifs):
        # In the simple form `iff.this` is a VALUE to compare against, not a
        # predicate; it renders the same way and lands in the same column, which
        # is why the THEN alignment needs no special case.
        cond_text = render_expr(iff.this, dialect)
        val_text = render_expr(iff.args["true"], dialect)
        head_segs = [Seg(head), Seg("WHEN")] if i == 0 else [Seg("WHEN")]
        segs = [*head_segs, Seg(cond_text), Seg("THEN", scope=scope, kind="then"),
                Seg(val_text)]
        lines.append(Line(anchor if i == 0 else when_col, segs))
    if default is not None:
        lines.append(Line(when_col, [Seg("ELSE"), Seg(render_expr(default, dialect))]))
    lines.append(Line(item_col, [Seg("END")]))
    return lines


def long_form_case_lines(case_node, ifs, default, anchor, when_col, dialect, *,
                         head, end_indent, end_suffix="") -> list[Line]:
    """Long-form CASE geometry, shared by this module (a root select-item CASE)
    and expr.py (a CASE broken inside a wrapping call chain, sample 21). The
    WHEN/AND/THEN math is identical either way: the first WHEN rides line 0, each
    subsequent WHEN/ELSE opens its own row at `when_col`, THEN drops to its own
    row at `when_col + 2`, a compound condition right-justifies AND/OR under WHEN,
    and each comparison operator is resolver-tagged (all via
    `_when_condition_lines`).

    Only three things vary between the two callers, so they are parameters rather
    than a re-derivation:
      - `head`   — line 0's opening seg: `"CASE"` for a root item, or the wrapper
                   prefix glued to CASE (`"COALESCE(SUM(CASE"`) for sample 21, so
                   CASE lands at its shifted column via render()'s own spacing.
      - `end_indent`: where `END` sits: `item_col` (under CASE) for a root item,
                   or CASE-col+1 for sample 21's tuck.
      - `end_suffix` — text glued directly onto `END` (the wrapper's tucked
                   closers, e.g. `"), 0.00)"`); empty for a root item.
    `when_col` is already the (possibly shifted) WHEN column, so the caller
    controls the horizontal offset without this function knowing about wrappers.
    """
    scope = f"case@{id(case_node)}"
    lines = []
    for i, iff in enumerate(ifs):
        cond_lines = _when_condition_lines(iff.this, scope, when_col, dialect)
        if i == 0:
            cond_lines[0] = Line(anchor, [Seg(head), *cond_lines[0].segs])
        val_text = render_expr(iff.args["true"], dialect)
        lines.extend(cond_lines)
        lines.append(Line(when_col + 2, [Seg("THEN"), Seg(val_text)]))
    if default is not None:
        lines.append(Line(when_col, [Seg("ELSE"), Seg(render_expr(default, dialect))]))
    lines.append(Line(end_indent, [Seg("END" + end_suffix)]))
    return lines


def _when_condition_lines(cond, scope, when_col, dialect) -> list[Line]:
    """`WHEN <cond>` (long form): `condition_block`'s own boolean-alignment
    geometry (max keyword width, right-justified so AND/OR end where WHEN
    ends; joiner placement shared verbatim via `joiner_head`). Only the LOOP
    is local rather than a call to `condition_block` itself: see the module
    docstring's DISCREPANCY note on why the `predicate_segs`-decline fallback
    below must stay local to CASE's WHEN, never shared with conditions.py's
    WHERE/HAVING/ON path.
    """
    conditions, joiner = split_conjunction(cond)
    keywords = ["WHEN"] + ([joiner] * (len(conditions) - 1) if joiner else [])
    max_kw = max(len(k) for k in keywords)
    lines = []
    for i, (kw, c) in enumerate(zip(keywords, conditions, strict=False)):
        try:
            segs = predicate_segs(c, scope, dialect)
        except Unsupported:
            segs = [Seg(render_expr(c, dialect))]
        head = Seg(kw.rjust(max_kw)) if i == 0 else joiner_head(kw, max_kw, lines[-1].segs)
        lines.append(Line(when_col, [head, *segs]))
    return lines
