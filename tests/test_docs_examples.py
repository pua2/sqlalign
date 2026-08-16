"""Every SQL example on the homepage is real formatter output, not prose.

`docs/index.html` sells one claim — *this is what sqlalign does to your SQL* — so
a hand-tuned example there is a lie the rest of the test suite cannot catch. Each
showcased block therefore carries `data-example="<id>"`, pairing it with the input
in `tests/fixtures/docs/<id>.sql`, and this module re-runs the formatter and
compares bytes. An `<id>` with no fixture, or a fixture no block references, fails
too — so an example can never quietly drift out of coverage.

Block attributes:
  data-example  fixture stem (required)
  data-role     "input" — the block is the fixture verbatim (the "Before" pane)
  data-preset   preset name, default "house"
  data-dialect  dialect, default "postgres"
  data-lines    "a-b", 1-based inclusive: the block shows only that slice of the
                output (a FROM-block excerpt, say), not the whole statement
"""
import html
import re

import pytest
from conftest import FIXTURES

from sqlalign.formatter import format_sql
from sqlalign.style import preset_style

HOMEPAGE = FIXTURES.parent.parent / "docs" / "index.html"
DOC_FIXTURES = FIXTURES / "docs"

# `<pre class="code" …>…</pre>`, capturing the attributes and the raw body.
_BLOCK = re.compile(r'<pre class="code"([^>]*)>(.*?)</pre>', re.S)
_ATTR = re.compile(r'data-([a-z]+)="([^"]*)"')
# Presentation-only markup the page adds for syntax colour; never part of the SQL.
_TAGS = re.compile(r"</?[bi]>")


def _blocks():
    """(attrs, sql) for every homepage block tagged with `data-example`."""
    out = []
    for m in _BLOCK.finditer(HOMEPAGE.read_text()):
        attrs = dict(_ATTR.findall(m.group(1)))
        if "example" in attrs:
            out.append((attrs, html.unescape(_TAGS.sub("", m.group(2)))))
    return out


BLOCKS = _blocks()


def _label(case):
    attrs = case[0]
    return f"{attrs['example']}-{attrs.get('role', 'output')}"


@pytest.mark.parametrize("case", BLOCKS, ids=_label)
def test_homepage_block_matches_the_formatter(case):
    attrs, shown = case
    source = (DOC_FIXTURES / f"{attrs['example']}.sql").read_text()

    if attrs.get("role") == "input":
        assert shown == source.rstrip("\n"), "the Before pane is not the fixture verbatim"
        return

    result = format_sql(
        source,
        attrs.get("dialect", "postgres"),
        preset_style(attrs.get("preset", "house")),
    )
    assert result.warnings == [], "a showcased example must not decline"

    expected = result.text.rstrip("\n").split("\n")
    if lines := attrs.get("lines"):
        lo, hi = (int(n) for n in lines.split("-"))
        expected = expected[lo - 1:hi]
    assert shown == "\n".join(expected)


def test_every_doc_fixture_is_shown_and_every_block_has_one():
    on_page = {attrs["example"] for attrs, _ in BLOCKS}
    on_disk = {p.stem for p in DOC_FIXTURES.glob("*.sql")}
    assert on_page == on_disk


def test_the_guides_mark_the_columns_the_formatter_produced():
    """The hero's three ochre hairlines are positioned in `ch` against columns
    read off the real output. If alignment geometry ever moves, the guides must
    move with it — this pins the two together."""
    hero = next(sql for attrs, sql in BLOCKS
                if attrs["example"] == "hero" and attrs.get("role") != "input")
    lines = hero.split("\n")
    # The table name, then the padding, then the alias.
    tables = [re.match(r"(?:FROM|(?:INNER|LEFT) JOIN) \S+ +(\S+)", ln) for ln in lines]
    # Join conditions only: the WHERE clause's own `AND` aligns to its own,
    # narrower column and must not be mistaken for one of these.
    joins = [re.search(r"\b(?:ON|AND) (\w+\.\w+) +=", ln) for ln in lines]
    tables = [m for m in tables if m]
    conditions = [m for m in joins if m and m.start() >= len("LEFT JOIN ")]
    assert len(tables) == 3 and len(conditions) == 3

    # Each column is shared by every row that participates in it — that sharing
    # is the alignment, and it is what a guide line is drawn to annotate.
    (alias,) = {m.start(1) for m in tables}
    (operand,) = {m.start(1) for m in conditions}
    (operator,) = {m.end() - 1 for m in conditions}
    assert (alias, operand, operator) == (29, 37, 55)

    # The hairlines sit half a character left of each, in the blank gutter.
    guides = re.search(r"background-position: ([^;]+);", HOMEPAGE.read_text()).group(1)
    assert guides == f"{alias - .5:g}ch 0, {operand - .5:g}ch 0, {operator - .5:g}ch 0"


# ---- the site's own encoding ----------------------------------------------

def test_the_homepage_declares_its_charset():
    """It did not, and the page is UTF-8 with 27 em-dashes in it.

    With no `charset` the browser falls back to a single-byte encoding — on the
    reported sighting, Windows-1252 — and every `—` renders as `â€”`:

        >>> "differ semantically — or if".encode("utf-8").decode("cp1252")
        'differ semantically â€” or if'

    The declaration has to come before any non-ASCII byte (the spec gives it the
    first 1024, and browsers stop sniffing at the first one they hit). The very
    first line of this file is a `<title>` containing an em-dash, so first line
    is the only safe position.
    """
    import pathlib

    page = pathlib.Path(__file__).resolve().parent.parent / "docs/index.html"
    raw = page.read_bytes()
    assert raw.startswith(b'<meta charset="utf-8">'), "charset is not the first thing"

    head, _, _ = raw.partition(b'<meta charset="utf-8">')
    assert head == b"", "something precedes the charset declaration"
    text = raw.decode("utf-8")
    # Five distinct characters were affected, not just the em-dash:
    #   —  x12  ->  â€”      ·  x9  ->  Â·       →  x3  ->  â†’
    #   │  x2   ->  â”‚      …  x1  ->  â€¦
    assert any(ord(ch) > 127 for ch in text), (
        "the page is pure ASCII now; this test is guarding nothing")


def test_no_other_served_html_is_missing_one():
    """If a second page is ever added it needs the same line."""
    import pathlib

    docs = pathlib.Path(__file__).resolve().parent.parent / "docs"
    for page in docs.rglob("*.html"):
        raw = page.read_bytes()
        if not any(b > 0x7F for b in raw):
            continue                      # pure ASCII cannot be mojibaked
        assert b'<meta charset="utf-8">' in raw[:1024], f"{page.name} has no charset"
