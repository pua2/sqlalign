"""Template-expression protection (Jinja / dbt).

A dbt model is not valid SQL — `select * from {{ ref('orders') }}` does not parse,
so sqlalign could not format one at all: it declined and passed the file through.
This module masks each template expression with a placeholder, lets the normal
engine format the result, then puts the original text back.

**Placeholders are LENGTH-PRESERVING, and that is the whole design.** sqlalign
computes alignment columns from rendered text widths, so a placeholder shorter or
longer than what it stands for would produce columns that are wrong the moment the
original is restored: every alias in a `FROM` block shifted by the difference
between `{{ ref('orders') }}` and whatever replaced it. Masking to the exact same
character count means every column is computed against the real width and survives
restoration untouched.

A template expression too short to hold a unique placeholder (fewer than
`_MIN_WIDTH` characters, e.g. `{{x}}`) is declined rather than approximated: the
statement passes through byte-identical, which is the house rule for anything the
engine cannot reproduce exactly.

The safety net compares the MASKED input against the MASKED output, not the raw
text: raw templated SQL does not parse, so there would be nothing to compare.
Since the mask is a pure bijection over the same character positions, equality of
the masked forms is equality of the originals.
"""
import re

# Jinja statement/expression/comment forms, in the order they must be tried.
DEFAULT_PATTERNS = (
    r"\{\{.*?\}\}",      # {{ ref('orders') }}
    r"\{%.*?%\}",        # {% if ... %}
    r"\{#.*?#\}",        # {# comment #}
)

_PREFIX = "_sqla_tpl_"
_MIN_WIDTH = len(_PREFIX) + 2        # prefix + at least one index digit + one pad


def _mask_for(index: int, width: int) -> str | None:
    """A placeholder of exactly `width` characters that lexes as one identifier.

    The `_` closing the stem is load-bearing: it keeps no placeholder a prefix of
    another (`_sqla_tpl_1_…` vs `_sqla_tpl_11_…`), and `unmask` substitutes them
    one at a time by plain text replace, which a prefix would corrupt.
    """
    stem = f"{_PREFIX}{index}_"
    if width < len(stem):
        return None
    return stem + "x" * (width - len(stem))


def mask(text: str, patterns=DEFAULT_PATTERNS) -> tuple[str, dict[str, str]]:
    """Replace every template expression with a same-width placeholder.

    Returns `(masked_text, {placeholder: original})`. Raises `ValueError` if any
    expression is too short to mask, or if a generated placeholder would collide
    with text already present: either way the caller passes the file through
    rather than risking a wrong reconstruction.
    """
    combined = re.compile("|".join(f"(?:{p})" for p in patterns), re.DOTALL)
    replacements: dict[str, str] = {}
    counter = 0

    def _replace(match: re.Match) -> str:
        nonlocal counter
        original = match.group(0)
        if len(original) < _MIN_WIDTH:
            raise ValueError(f"template expression too short to mask: {original!r}")
        placeholder = _mask_for(counter, len(original))
        counter += 1
        if placeholder is None or placeholder in text:
            raise ValueError(f"cannot mask template expression: {original!r}")
        replacements[placeholder] = original
        return placeholder

    return combined.sub(_replace, text), replacements


def unmask(text: str, replacements: dict[str, str]) -> str:
    """Put the original template expressions back."""
    for placeholder, original in replacements.items():
        text = text.replace(placeholder, original)
    return text


def has_templating(text: str, patterns=DEFAULT_PATTERNS) -> bool:
    return any(re.search(p, text, re.DOTALL) for p in patterns)
