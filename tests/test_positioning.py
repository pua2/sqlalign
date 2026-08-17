"""What the front page promises, checked against what the tool does.

Positioning drifts more quietly than code. A README keeps selling the thing the
project was a year ago, and nothing fails — which is how the docs came to say
comments were excluded from the safety net months after they stopped being.

These are narrow on purpose. They do not police prose; they assert the handful of
claims that would be actively wrong if the code moved underneath them.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
GUIDE = {p.name: p.read_text() for p in (ROOT / "docs" / "guide").glob("*.md")}


def test_nothing_still_says_comments_are_outside_the_guarantee():
    """The claim that stopped being true in 1.1, and the reason this file exists.

    Comments are compared now, so a document saying they are excluded is telling
    a reader the tool is weaker than it is -- and telling a contributor that a
    dropped comment is acceptable.
    """
    stale = re.compile(r"comments? (are|is) excluded|comment cannot change meaning", re.I)
    for name, text in {**GUIDE, "README.md": README}.items():
        assert not stale.search(text), f"{name} still says comments are outside the net"


def test_the_readme_says_what_means_is_measured_by():
    """"Cannot change what your SQL means" is only honest if the page says what
    is doing the measuring. Otherwise it reads as a claim about intent."""
    assert "cannot" in README and "syntax tree" in README


def test_the_readme_names_the_two_spellings_the_guarantee_cannot_reach():
    """`!=`/`<>` and `decimal`/`numeric` collapse at parse time, so sqlalign
    picks them. A guarantee that quietly excludes two things is worse than one
    that names them."""
    assert "neq" in README.lower() or "`!=`" in README
    assert "decimal" in README.lower()


def test_the_readme_leads_with_diff_before_the_in_place_form():
    """Nobody should have a repository reflowed before seeing what the style
    does to it."""
    assert README.index("sqlalign --diff") < README.index("sqlalign query.sql")


def test_the_readme_names_the_dollar_body_linting():
    """The one thing nothing else does. It was buried under alignment, which is
    the thing several tools do."""
    assert "--lint" in README
    assert "sqlfluff cannot" in README


def test_the_dbt_section_states_which_warehouses_actually_parse():
    """The `dbt` keyword pulls in users whose warehouse sqlalign cannot parse.
    Saying so up front costs a little reach and saves their afternoon."""
    section = README[README.index("## dbt / Jinja"):]
    assert "Snowflake" in section[:800], "the gap is not named where it is found"


def test_the_diff_churn_of_alignment_is_named():
    """Inherent to columnar alignment: one longer alias reflows a block. A team
    that discovers it during their first review will blame the tool."""
    assert "diff" in README.lower() and "padding" in README.lower()


def test_compact_is_offered_to_teams_with_a_style_guide():
    assert "compact" in README
    assert "published" in README


def test_the_signed_provenance_is_mentioned():
    """Already true of every release and worth saying, for a tool that rewrites
    source files."""
    assert "provenance" in README.lower()


# ---- CONTRIBUTING, which a contributor reads before anything else -----------

def test_the_stated_test_count_is_roughly_right():
    """`~4,000 tests` is a claim a contributor checks against their own run in
    the first minute. Compared loosely on purpose: an exact number would need
    editing in every pull request, and a claim that churns is one nobody trusts.
    """
    import subprocess
    import sys

    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    claimed = int(re.search(r"~([\d,]+) tests", contributing).group(1).replace(",", ""))
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--collect-only",
                           str(ROOT / "tests")], capture_output=True, text=True, cwd=ROOT)
    actual = int(re.search(r"(\d+) tests? collected", proc.stdout).group(1))
    assert 0.8 * actual <= claimed <= 1.2 * actual, (
        f"CONTRIBUTING says ~{claimed:,}, the suite collects {actual:,}")


def test_contributing_lists_the_jobs_ci_actually_has():
    """A contributor debugging a red build looks here first. A job missing from
    the table is one they will not know to look at."""
    import yaml

    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    jobs = set(yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())["jobs"])
    for job in jobs - {"test"}:
        assert f"`{job}`" in contributing, f"CI job {job} is not described"


def test_every_alignment_target_is_documented_everywhere_targets_are_listed():
    """The docs listed six of nine for a full release: `column_aliases`,
    `table_aliases` and `table_names` were missing from three pages and from
    the flag's own help, which now derives its list instead of typing it."""
    from sqlalign.style import ALL_ALIGN_TARGETS

    for page in ("cli.md", "style.md"):
        text = GUIDE[page]
        for target in ALL_ALIGN_TARGETS:
            assert f"`{target}`" in text, f"{page} does not document {target}"


def test_the_stated_golden_count_is_the_actual_golden_count():
    """Two pages said 25 while two others said 29. Exact on purpose: goldens
    change rarely and deliberately, so the number should only move in the same
    commit that moves them."""
    actual = len(list((ROOT / "tests" / "fixtures" / "expected").glob("*.sql")))
    for name, text in {**GUIDE, "README.md": README}.items():
        for match in re.finditer(r"(\d+) hand-formatted", text):
            assert int(match.group(1)) == actual, (
                f"{name} says {match.group(1)} goldens; there are {actual}")


def test_the_decline_examples_in_the_guide_actually_decline():
    """Three worked examples showed DISTINCT ON and JOIN USING declining months
    after both started formatting. Every fenced statement a guide page presents
    as a decline is run here; one that formats is a page teaching users to
    expect warnings they will never see."""
    from sqlalign.formatter import format_sql

    examples = {
        "faq.md": "with c(a, b) as (select 1, 2) select a from c;",
        "architecture.md": "with c(a, b) as (select 1, 2) select a from c;",
        "style.md": "with c(a, b) as (select 1, 2) select a from c;",
    }
    for page, sql in examples.items():
        assert sql in GUIDE[page], f"{page} no longer shows {sql!r}; update this map"
        result = format_sql(sql, "postgres")
        assert result.declines, f"{page} presents {sql!r} as a decline, but it formats"


def test_the_exit_code_table_matches_the_gates():
    """`--max-declines` and `--lint` return 1 on their own findings; the table
    said only --check/--diff ever did, so CI reading 1 as "would reformat"
    misread both."""
    cli = GUIDE["cli.md"]
    row = next(line for line in cli.splitlines() if line.startswith("| `1` |"))
    for gate in ("--check", "--diff", "--max-declines", "--lint"):
        assert gate in row, f"the exit-1 row does not mention {gate}"
    assert "141" in cli, "the SIGPIPE exit is undocumented"


def test_the_shipped_hooks_are_documented():
    """.pre-commit-hooks.yaml and action.yml shipped in 1.1 with no mention in
    the README or the guide -- an adoption lever nobody could find."""
    assert "pre-commit" in README
    assert "pua2/sqlalign@" in README, "the action is not shown in the README"
    started = GUIDE["getting-started.md"]
    assert "id: sqlalign" in started
    assert "uses: pua2/sqlalign@" in started
