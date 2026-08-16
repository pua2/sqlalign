from dataclasses import dataclass, field


@dataclass
class Seg:
    text: str
    scope: str | None = None
    kind: str | None = None    # item|op|alias|on|as|table|type|constraint|then


@dataclass
class Line:
    indent: int
    segs: list[Seg] = field(default_factory=list)


RIGHT_ALIGNED = {"op", "on"}

# A comment segment. Tagged so the sites that glue punctuation onto the END of a
# row: `joiner_head` under trailing booleans, commas.py under trailing commas --
# can place it before the comment. Appending blindly to the last segment puts
# the punctuation inside a `--` comment, so the row loses its separator.
#
# `kind` alone is inert for alignment: align.py only aligns a segment carrying
# BOTH a scope and a kind, and a comment never gets a scope.
COMMENT_KIND = "comment"


def comment_seg(text: str) -> "Seg":
    return Seg(text, kind=COMMENT_KIND)


def split_trailing_comments(segs: list["Seg"]) -> tuple[list["Seg"], list["Seg"]]:
    """`(content, trailing comments)` -- the comment run at the end of a row."""
    cut = len(segs)
    while cut and segs[cut - 1].kind == COMMENT_KIND:
        cut -= 1
    return segs[:cut], segs[cut:]
