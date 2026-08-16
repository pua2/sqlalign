from sqlalign.ir import RIGHT_ALIGNED, Line, Seg
from sqlalign.style import ALIGN_TARGETS, ALL_ALIGN_TARGETS


def apply_align_targets(lines: list[Line], targets: frozenset[str]) -> list[Line]:
    """Untag every segment whose alignment target is disabled.

    One mechanism serves all of them: a segment only participates in alignment
    when it carries BOTH a scope and a kind (see `_resolve_targets`), so
    switching a target off is just clearing those two fields. The column then
    collapses to render()'s ordinary single space -- no separate code path, and
    no way for a disabled target to still influence a column.
    """
    if targets == ALL_ALIGN_TARGETS:
        return lines
    enabled = frozenset().union(*(ALIGN_TARGETS[t] for t in targets)) if targets else frozenset()
    for line in lines:
        for seg in line.segs:
            if seg.kind is not None and seg.scope is not None and seg.kind not in enabled:
                seg.scope = seg.kind = None
    return lines


def _placed(line: Line) -> list[Seg]:
    """The segments of `line` that actually occupy a column.

    A zero-width segment is dropped unless it is TAGGED. The distinction is real:
    a handler emits `Seg("", scope, kind=...)` deliberately, to hold a column with
    nothing in it: fromjoin.py heads a trailing-boolean continuation that way so
    the resolver right-aligns its zero-width end into the ON column. An UNTAGGED
    empty segment holds no column and renders nothing, yet the one-space
    inter-segment separator would still emit a space in front of it.

    Those are not two separate cases: `apply_align_targets` turns the first into
    the second by clearing scope and kind, so a disabled target leaves no
    phantom space where its column was and `align_targets=frozenset()` agrees
    with `align=False`.

    Both passes below must filter identically, or the resolver counts a separator
    the emitter does not write and every target shifts by one.
    """
    return [seg for seg in line.segs if seg.text or (seg.scope and seg.kind)]


def _resolve_targets(lines: list[Line]) -> dict[tuple[str, str], int]:
    """Per (scope, kind) group, find the target column, dependency-aware.

    A tag's target is the max, over the group's segments, of where that segment
    *lands*: its start column for a left-aligned kind, its end column for a
    RIGHT_ALIGNED one. Landing is not the same as the segment's naive
    single-space position: an EARLIER tagged segment on the same line may itself
    be padded rightward (out to its own group target) in the emit pass, which
    shifts everything after it. So a tag's position depends on the targets of
    the tags to its left, which is why a single left-to-right sweep (as the
    original resolver did) under-computes a column whenever two dependent tagged
    columns share a line and their maxima are driven by different rows.

    We iterate to a fixpoint. Each sweep recomputes every target by replaying
    the emit pass's padding with the *previous* sweep's targets: when the
    running column reaches a tagged segment, place it at
    `max(natural, targets_so_far)` and advance past the placed (not natural)
    position. Targets are monotonically non-decreasing across sweeps and bounded
    by total line length, so the iteration converges; we stop as soon as a full
    sweep changes nothing.

    Convergence is exact and cheap for the common case: the FIRST sweep starts
    from all-zero targets, so every `max(natural, 0)` is just `natural` and the
    result is byte-identical to the original single-sweep resolver. A second
    sweep only raises a target when some tagged segment actually sat behind an
    earlier padded tagged segment on its line; lines with at most one tagged
    column (01/02/04 and every non-FROM/JOIN scope) never trigger that, so they
    fix in one sweep at exactly the original targets. The enhancement can only
    raise an under-computed column, never lower or alter a correct one.
    """
    targets: dict[tuple[str, str], int] = {}
    num_keys = len({(s.scope, s.kind) for ln in lines for s in ln.segs
                    if s.scope and s.kind})
    # Monotone, bounded targets converge; this cap only guards a logic error.
    cap = len(lines) * num_keys + 8
    for _ in range(cap):
        swept: dict[tuple[str, str], int] = {}
        for line in lines:
            col = line.indent
            for i, seg in enumerate(_placed(line)):
                if i:
                    col += 1                   # one separating space
                if seg.scope and seg.kind:
                    key = (seg.scope, seg.kind)
                    if seg.kind in RIGHT_ALIGNED:
                        placed_end = max(col + len(seg.text), targets.get(key, 0))
                        swept[key] = max(swept.get(key, 0), placed_end)
                        col = placed_end
                    else:
                        placed_start = max(col, targets.get(key, 0))
                        swept[key] = max(swept.get(key, 0), placed_start)
                        col = placed_start + len(seg.text)
                else:
                    col += len(seg.text)
        if swept == targets:
            return targets
        targets = swept
    raise AssertionError("alignment resolver did not converge to a fixpoint")


def render(lines: list[Line], align: bool = True) -> str:
    """Emit `lines` as text. With `align` False the column-resolution pass is
    skipped entirely and segments are emitted one space apart: line breaks and
    indents are already fixed by the layout handlers, so this yields the same
    line structure without the alignment padding (Style.align).

    every empty segment is dropped here, including a tagged one: a stronger rule
    than `_placed`'s, and deliberately so. A handler emits `Seg("", kind="on")`
    purely to hold a column (fromjoin.py heads a trailing-boolean continuation
    that way), and with alignment off there are no columns for it to hold. Joining
    it like any other segment contributed a separator space with no text in front
    of it, so an unaligned continuation indented THREE where the structure calls
    for two."""
    if not align:
        return "\n".join(
            (" " * line.indent + " ".join(seg.text for seg in line.segs if seg.text)).rstrip()
            for line in lines
        ) + "\n"

    # Pass 1: per (scope, kind) group, find the target column (dependency-aware).
    targets = _resolve_targets(lines)

    # Pass 2: emit, padding tagged segments out to their group target.
    out = []
    for line in lines:
        buf, col = [" " * line.indent], line.indent
        for i, seg in enumerate(_placed(line)):
            if i:
                buf.append(" ")
                col += 1
            if seg.scope and seg.kind:
                t = targets[(seg.scope, seg.kind)]
                start = t - len(seg.text) if seg.kind in RIGHT_ALIGNED else t
                pad = start - col
                if pad > 0:
                    buf.append(" " * pad)
                    col += pad
            buf.append(seg.text)
            col += len(seg.text)
        out.append("".join(buf).rstrip())
    return "\n".join(out) + "\n"
