"""Long nested-expression breaking 

A select item whose value is a call chain wrapping a single breakable CASE
(sample 21: `COALESCE(SUM(CASE … END), 0.00) AS funding_amt`) and whose flat
one-line render exceeds the width limit breaks AT the CASE: the wrapper prefix
(`COALESCE(SUM(`) rides line 0 directly before `CASE`, the CASE lays out
long-form (reusing case.py's `long_form_case_lines`) at columns SHIFTED right by
the prefix, and the wrapper's closers (`), 0.00)`) tuck onto the END line, which
sits at CASE-col + 1.

The requirements are now only structural: exactly one CASE, not at the value root
(a root CASE is case.py's job), and a wrapper whose flat render splits cleanly
around the CASE's own flat render. The prefix/suffix are DERIVED from
`render_expr` (never hard-coded) by locating the CASE's rendered substring inside
the value's rendered form — so the tuck is guaranteed token-consistent with the
flat render. Anything else returns None and the item renders FLAT.

`END` sits one column right of `CASE`: `END` is three characters and `CASE` is
four, so the wrapper's closers -- glued straight onto it -- land in the column
immediately after the CASE keyword, where the CASE's own content starts on line
0:

        , COALESCE(SUM(CASE WHEN …          , MAX(CASE WHEN …
                             AND …                   AND …
                              THEN …                  THEN …
                        END), 0.00)             END) AS …

That is a function of the two keywords' widths, so it holds for any prefix.
"""
from sqlglot import exp

from sqlalign.casing import render_expr
from sqlalign.layout import column_alias, select_item_col
from sqlalign.layout.case import long_form_case_lines

_WHEN_OFFSET = len("CASE ")     # 5: WHEN sits one space after CASE (long form)

# Where `END` sits, relative to the CASE column. Not an arbitrary indent: `END`
# is three characters and `CASE` is four, so this puts the wrapper's closers --
# glued straight onto END: in the column immediately after the CASE keyword,
# which is where the CASE's own content starts on line 0. Read off sample 21's
# bytes, where it is the only relationship that holds.
_END_OFFSET = len("CASE") - len("END")   # 1


def nested_break_case(item, anchor, dialect, width):
    """The CASE node this item breaks at, or None if `item` is not an
    over-width nested-CASE-break shape. Pure predicate (no layout): select.py
    uses it both to route the item here and to exempt the wrapped CASE from its
    `_UNSUPPORTED_NODES` guard (a nested CASE is otherwise declined)."""
    value = item.this if isinstance(item, exp.Alias) else item
    matched = _match(value, dialect)
    if matched is None:
        return None
    _prefix, case_node, _suffix = matched
    if not _case_ok(case_node):
        return None
    if not _over_width(item, value, anchor, dialect, width):
        return None
    return case_node


def nested_break_body(item, anchor, dialect, width):
    """The item's CASE-broken `list[Line]` (line 0's indent == `anchor`, ready
    for select.py's `_multiline_item_lines` to splice the leading comma onto
    line 0 and tuck `AS alias` onto the END line), or None if `item` is not the
    recognized shape."""
    if nested_break_case(item, anchor, dialect, width) is None:
        return None
    value = item.this if isinstance(item, exp.Alias) else item
    prefix, case_node, suffix = _match(value, dialect)
    item_col = select_item_col(anchor)
    case_col = item_col + len(prefix)
    # The simple form's test operand rides line 0 with CASE and pushes the WHEN
    # column right by its width, exactly as it does for a root CASE (case.py).
    operand = case_node.args.get("this")
    head_text = "CASE" if operand is None else f"CASE {render_expr(operand, dialect)}"
    when_col = case_col + len(head_text) + 1
    return long_form_case_lines(
        case_node, case_node.args["ifs"], case_node.args.get("default"), anchor,
        when_col, dialect,
        head=prefix + head_text, end_indent=case_col + _END_OFFSET, end_suffix=suffix,
    )


def _match(value, dialect):
    """Locate the single wrapped CASE and split the value's flat render around
    it: `(prefix, case_node, suffix)`, or None. `prefix`/`suffix` are the exact
    rendered text before/after the CASE (e.g. `COALESCE(SUM(` / `), 0.00)`), so
    they compose byte-for-byte with the flat render every other item uses."""
    cases = [n for n in value.walk() if isinstance(n, exp.Case)]
    if len(cases) != 1:
        return None
    case_node = cases[0]
    if case_node is value:                       # a root CASE belongs to case.py
        return None
    flat_value = render_expr(value, dialect)
    flat_case = render_expr(case_node, dialect)
    idx = flat_value.find(flat_case)
    if idx < 0 or flat_value.find(flat_case, idx + 1) != -1:
        return None                              # not found, or ambiguous
    return flat_value[:idx], case_node, flat_value[idx + len(flat_case):]


def _case_ok(case_node):
    """Every CASE is modelled here now -- see this module's docstring on why the
    wrapper restriction lifted. A CASE with no WHENs cannot be parsed, so the
    only thing left to check is that there is something to lay out."""
    return bool(case_node.args.get("ifs"))


def _over_width(item, value, anchor, dialect, width):
    """Whether the item's flat one-line render (leading comma column through the
    alias) exceeds the width limit — the trigger to break at the CASE."""
    flat = render_expr(value, dialect)
    end = select_item_col(anchor) + len(flat)
    if isinstance(item, exp.Alias):
        end += 1 + len("AS " + column_alias(item, dialect))
    return end > width.limit(anchor)
