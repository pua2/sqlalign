"""The `--report` invitation, and the stability policy it sits beside.

A decline is a gap measured on someone's own SQL rather than guessed at, which
makes it the most useful thing a user could send this project. Left as a count it
is a dead end.
"""
import pathlib
import re

import pytest

from sqlalign.cli import _ask_for_construct, _issue_url, _report

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _causes(**kinds):
    return {(kind, reason): count for kind, pairs in kinds.items()
            for reason, count in pairs.items()}


def test_the_report_invites_a_request_for_the_top_construct():
    text = _report(10, 4, _causes(unsupported={"PIVOT: this dialect has no such syntax": 4}))
    assert "PIVOT is the construct" in text
    assert "issues/new?" in text


def test_the_issue_title_carries_the_construct_and_not_the_explanation():
    """A reason reads `PIVOT: this dialect has no such syntax`. Splicing the
    whole thing into a title reads like a machine wrote it."""
    ask = _ask_for_construct(_causes(unsupported={"PIVOT: this dialect has no such syntax": 2}))
    assert "title=Support+PIVOT&" in ask
    assert "syntax" not in ask.split("title=")[1]


def test_only_the_most_common_construct_is_offered():
    """A URL per cause turns a summary into a wall of links."""
    ask = _ask_for_construct(_causes(unsupported={"PIVOT": 9, "MERGE": 2, "CURSOR": 1}))
    assert ask.count("issues/new?") == 1
    assert "PIVOT" in ask


@pytest.mark.parametrize("kind", ["parse", "upstream", "safety", "error"])
def test_no_invitation_for_declines_that_are_not_ours_to_implement(kind):
    """A parse error is the author's SQL, an upstream failure is sqlglot's, and
    a safety decline is a bug rather than a feature request."""
    assert _ask_for_construct(_causes(**{kind: {"something": 5}})) is None


def test_no_invitation_when_nothing_declined():
    assert _ask_for_construct({}) is None
    assert "issues/new" not in _report(10, 0, {})


def test_the_address_comes_from_the_package_metadata():
    """Written in pyproject and nowhere else, so it cannot drift from what PyPI
    shows."""
    url = _issue_url()
    assert url is None or url.endswith("/issues")


# ---- the stability policy --------------------------------------------------

def test_the_policy_is_written_down():
    """A team pinning a version needs to know what a patch upgrade can do to
    their repository, and the answer only counts if it is published."""
    style = (ROOT / "docs" / "guide" / "style.md").read_text()
    assert "## Stability" in style
    assert "does not change in a patch release" in style


def test_the_policy_names_the_mechanism_that_enforces_it():
    """It is a property, not a promise: the goldens are byte-exact and run as
    their own CI job. Saying so is what makes it credible."""
    style = (ROOT / "docs" / "guide" / "style.md").read_text()
    assert "byte for byte" in style
    assert "CI job" in style


def test_the_goldens_really_do_run_as_their_own_job():
    """The claim above, checked against the workflow rather than trusted."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert re.search(r"^  goldens:", workflow, re.M), "no goldens job"
    assert "goldens_unchanged" in workflow
