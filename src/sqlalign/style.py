"""Style options. Layout handlers read these rather than hard-coding literals."""
from dataclasses import dataclass, field

from sqlalign.config import Width

# User-facing alignment targets, mapped to the internal segment kinds that
# implement them. Names are config-file surface: adding one is safe, renaming
# one is a breaking change.
ALIGN_TARGETS: dict[str, frozenset[str]] = {
    "aliases":            frozenset({"as", "alias"}),        # both of the next two
    "column_aliases":     frozenset({"as"}),                 # `x AS foo`
    "table_aliases":      frozenset({"alias"}),              # `FROM orders o`
    "table_names":        frozenset({"table"}),              # the table after FROM/JOIN
    "operators":          frozenset({"op"}),                 # =, !=, <, LIKE, IS ...
    "join_conditions":    frozenset({"on"}),                 # the ON/AND column
    "case_results":       frozenset({"then"}),               # THEN in a short-form CASE
    "column_types":       frozenset({"type"}),               # CREATE TABLE types
    "column_constraints": frozenset({"constraint", "nul", "enc"}),
}

ALL_ALIGN_TARGETS = frozenset(ALIGN_TARGETS)

# `align` alone enables every target except `table_names`, which pads the
# FROM/JOIN keyword so table names share a column: a different published style
# rather than a refinement of this one. It remains a legal setting.
HOUSE_ALIGN_TARGETS = ALL_ALIGN_TARGETS - {"table_names"}

# Dialects whose emitted keywords are verified valid.
#
# The AST check cannot catch dialect-invalid output. sqlglot parses more
# leniently than any single engine: T-SQL `SELECT TOP 10 id` yields the same
# `Limit` node Postgres uses, the layout emits `LIMIT`, and sqlglot reads that
# back as T-SQL too, so the comparison passes and SQL Server rejects the
# result. Adding a dialect means auditing every keyword the handlers emit.
SUPPORTED_DIALECTS = frozenset({"postgres", "redshift", "tsql"})


@dataclass(frozen=True)
class Style:
    width: Width = field(default_factory=Width)

    # Master switch. Off emits every segment one space apart, keeping the
    # same line structure without the padding.
    align: bool = True

    # Which targets participate when `align` is on. An omitted target is
    # emitted untagged, so its column collapses to single spaces.
    align_targets: frozenset[str] = field(default_factory=lambda: HOUSE_ALIGN_TARGETS)

    # Whether to format inside dollar-quoted ($$) plpgsql bodies. Off passes
    # the whole CREATE FUNCTION/PROCEDURE through byte-identical.
    format_dollar_bodies: bool = True

    # Output spelling for the two distinctions sqlglot collapses at parse time,
    # so one has to be chosen when printing. Everything it preserves: cast
    # form, GROUP BY references, alias presence, identifier case: is passed
    # through untouched and is not a knob.
    neq_style: str = "!="            # "!=" | "<>"
    decimal_style: str = "NUMERIC"   # "NUMERIC" | "DECIMAL"

    # `FROM orders o` or `FROM orders AS o`. A third collapsed spelling: both
    # forms parse identically, so the printed one is chosen here.
    table_alias_style: str = "bare"  # "bare" | "as"

    # Where the separator sits in a stacked list.
    comma_position: str = "leading"  # "leading" | "trailing"

    # Where AND/OR sit when a predicate spans lines.
    boolean_operator_position: str = "leading"  # "leading" | "trailing"

    # Whether a JOIN's ON rides the table-reference line or drops below it.
    # `own_line` retires the FROM-block-global ON column, so `join_conditions`
    # has nothing to act on.
    on_placement: str = "inline"     # "inline" | "own_line"

    # Whether the first select item rides the SELECT line.
    select_placement: str = "inline"   # "inline" | "own_line"

    # `river` right-aligns root keywords so their last character lands on the
    # gutter, putting every clause body in one column. A keyword wider than the
    # gutter hangs on the far side.
    clause_keyword_align: str = "left"   # "left" | "river"

    # The column a river's keywords right-align to. 6 is the width of SELECT.
    river_gutter: int = 6

    # List indent under `select_placement="own_line"`. Ignored when inline,
    # where the column is len("SELECT ").
    select_indent: int = 2

    # Blank lines between the elements of a plpgsql body (DECLARE, BEGIN, each
    # statement, END). Separate from `blank_lines_between_statements`.
    body_blank_lines: int = 1

    # Mask Jinja/dbt expressions ({{ }}, {% %}, {# #}) so a templated model
    # parses. Placeholders keep the original width, so alignment columns are
    # computed against the real text. Harmless when absent.
    protect_templating: bool = True

    # Case for keywords, function names and type names. Identifiers and string
    # literals are never touched.
    keyword_case: str = "upper"      # "upper" | "lower"

    # `None` applies the house rule: one blank line between two multi-line
    # statements, none otherwise. An integer forces that many between every pair.
    blank_lines_between_statements: int | None = None

    def __post_init__(self):
        if self.keyword_case not in ("upper", "lower"):
            raise ValueError(
                f"keyword_case must be 'upper' or 'lower', got {self.keyword_case!r}")
        blanks = self.blank_lines_between_statements
        if blanks is not None and (not isinstance(blanks, int) or isinstance(blanks, bool)
                                   or blanks < 0):
            raise ValueError("blank_lines_between_statements must be a non-negative "
                             f"integer or unset, got {blanks!r}")
        unknown = sorted(set(self.align_targets) - ALL_ALIGN_TARGETS)
        if unknown:
            raise ValueError(
                f"unknown align_targets {unknown}; valid: {sorted(ALL_ALIGN_TARGETS)}")
        if self.comma_position not in ("leading", "trailing"):
            raise ValueError(
                f"comma_position must be 'leading' or 'trailing', got {self.comma_position!r}")
        if self.boolean_operator_position not in ("leading", "trailing"):
            raise ValueError("boolean_operator_position must be 'leading' or 'trailing', "
                             f"got {self.boolean_operator_position!r}")
        if self.on_placement not in ("inline", "own_line"):
            raise ValueError(
                f"on_placement must be 'inline' or 'own_line', got {self.on_placement!r}")
        if (not isinstance(self.body_blank_lines, int)
                or isinstance(self.body_blank_lines, bool) or self.body_blank_lines < 0):
            raise ValueError("body_blank_lines must be a non-negative integer, got "
                             f"{self.body_blank_lines!r}")
        if self.clause_keyword_align not in ("left", "river"):
            raise ValueError("clause_keyword_align must be 'left' or 'river', got "
                             f"{self.clause_keyword_align!r}")
        if (not isinstance(self.river_gutter, int) or isinstance(self.river_gutter, bool)
                or self.river_gutter < 2):
            raise ValueError(f"river_gutter must be an integer >= 2, got {self.river_gutter!r}")
        if self.select_placement not in ("inline", "own_line"):
            raise ValueError("select_placement must be 'inline' or 'own_line', got "
                             f"{self.select_placement!r}")
        if (not isinstance(self.select_indent, int) or isinstance(self.select_indent, bool)
                or self.select_indent < 1):
            raise ValueError("select_indent must be a positive integer, got "
                             f"{self.select_indent!r}")
        if self.neq_style not in ("!=", "<>"):
            raise ValueError(f"neq_style must be '!=' or '<>', got {self.neq_style!r}")
        if self.decimal_style not in ("NUMERIC", "DECIMAL"):
            raise ValueError(
                f"decimal_style must be 'NUMERIC' or 'DECIMAL', got {self.decimal_style!r}")
        if self.table_alias_style not in ("bare", "as"):
            raise ValueError("table_alias_style must be 'bare' or 'as', got "
                             f"{self.table_alias_style!r}")


HOUSE = Style()
COMPACT = Style(align=False)

# Named starting points, so a team picks one word instead of reading nine knobs.
# A preset only sets a BASE: config-file keys and command-line flags still layer
# on top of it, so `preset = "compact"` plus `comma_position = "trailing"` is a
# legitimate combination rather than an either/or.
#
# The `dbt` preset was deliberately withheld until `keyword_case` existed, on the
# grounds that a preset getting the commas right and the casing wrong would look
# official while being wrong. With casing, comma position and padding all now
# expressible it is substantially accurate, and it ships with its one remaining
# deviation stated: dbt indents nested blocks 4, sqlalign indents a CTE body 2
# (see the dropped `indent_unit`: sqlalign's indent literals are three
# different concepts, not one knob).
PRESETS: dict[str, dict] = {
    # The columnar house style: aligned, leading separators, ON inline.
    "house": {},
    # Same line structure, no alignment padding. 9 of 10 published SQL style
    # guides produce unpadded output, so this is the widest-reach starting point.
    "compact": {"align": False},
    # Keeps the alignment but moves both separators to the end of the line, for
    # teams that align yet write trailing commas (8 of 10 guides) and trailing
    # booleans (7 of 10).
    "trailing": {"comma_position": "trailing",
                 "boolean_operator_position": "trailing"},
    # dbt / analytics-engineering conventions: lowercase everything, the select
    # list stacked under a bare `select` at 4: dbt's own guide, and the reason
    # this preset states the indent rather than taking the default 2: trailing
    # commas, no columnar padding. Deviation: a CTE body indents 2, not 4.
    "dbt": {"keyword_case": "lower", "comma_position": "trailing", "align": False,
            "select_placement": "own_line", "select_indent": 4},
    # Holywell's river (sqlstyle.guide) and its two verbatim forks. Table-alias
    # padding is off: that column would span rows at two different indents (FROM
    # sits in the gutter, JOINs on the far side of it) and stretch absurdly.
    # River is a structural style, not a columnar one.
    "river": {"clause_keyword_align": "river", "on_placement": "own_line",
              "align": False},
    # GitLab's published SQL style guide (handbook.gitlab.com, "SQL Style Guide"),
    # taken from the linter-processed "Example Code" section rather than the prose
    # examples where the two disagree: their .sqlfluff config and that section
    # both use a 2-space step, the prose uses 4.
    #
    # Their config asks for exactly one alignment ("aligning column aliases within
    # the SELECT statement"), which is why `align_targets` is that one target
    # rather than `align = false`: the rest of the line structure is still
    # sqlalign's, only the padding is theirs.
    "gitlab": {"comma_position": "trailing", "on_placement": "own_line",
               "select_placement": "own_line",
               "table_alias_style": "as",
               "align_targets": frozenset({"column_aliases"})},
}


def preset_style(name: str, **overrides) -> Style:
    """A Style from a named preset, with any keyword overrides applied on top."""
    if name not in PRESETS:
        raise ValueError(f"unknown preset {name!r}; valid: {sorted(PRESETS)}")
    return Style(**{**PRESETS[name], **overrides})
