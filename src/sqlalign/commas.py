"""Comma position (`Style.comma_position`) — an IR transform, not a text rewrite.

House style stacks a list with the separator comma leading each continuation
line; `trailing` moves it to the end of the preceding line:

    SELECT a              SELECT a,
         , b                     b
         , c                     c

**Why this is done on the IR rather than on printed text.** Both shipped
implementations of this option in the wild were post-hoc rewrites over
already-formatted strings, and both were removed by their own authors
(sql-formatter dropped `commaPosition` in v14). A string pass cannot reliably
tell a separator comma from a comma inside `NUMERIC(10, 2)`, a function call, or
a string literal, and it cannot know which physical line ends a multi-line item.
Here every separator comma is emitted by its handler as its own segment tagged
`kind="comma"` at the head of the continuation line, so this pass only has to
move a known segment: no parsing, no guessing.

The relocation target is simply the previous line, which is correct even for
multi-line items (a CASE, a scalar subquery, a window): the next item's
comma-head line always directly follows the previous item's LAST line, so
"previous line" *is* "where that item ended".

Tagged `kind="comma"` with no `scope`, so the alignment resolver ignores these
segments entirely (it only considers a segment with both a scope and a kind).
"""
from sqlalign.ir import Line, split_trailing_comments

COMMA_KIND = "comma"


def apply_comma_position(lines: list[Line], position: str) -> list[Line]:
    """Relocate separator commas per `position`. Mutates and returns `lines`."""
    if position == "leading":
        return lines                      # handlers already emit the house form
    for i, line in enumerate(lines):
        if not line.segs or line.segs[0].kind != COMMA_KIND:
            continue
        head = line.segs[0]
        # Blank the comma but keep its width, so the term stays in the same
        # column it occupies under leading commas.
        head.text = " " * len(head.text)
        # Walk back past blank lines: CTEs are separated by one, so the comma
        # belongs on the preceding CTE's `)` line, not on the empty line.
        j = i - 1
        while j >= 0 and not lines[j].segs:
            j -= 1
        if j >= 0:
            # Glued onto the existing segment's text, never appended as its own
            # Seg: render() puts a space between segs, which would give `a ,`.
            # Before any trailing comment, for the same reason `joiner_head`
            # does: a comma appended past a `--` is inside it, and the list
            # loses its separator.
            content, comments = split_trailing_comments(lines[j].segs)
            content[-1].text += ","
            lines[j].segs[:] = [*content, *comments]
    return lines
