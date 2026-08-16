"""What a user gets from `pip install sqlalign`, as opposed to what the source
tree does.

Nothing else in the suite looks at packaging metadata, so it is the one surface
where a claim can be wrong for a whole release without a single test noticing.
`1.0.1` shipped the `Typing :: Typed` classifier with no `py.typed` beside it,
which meant every type checker skipped the package while the metadata said the
opposite.
"""
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
PROJECT = PYPROJECT["project"]


def test_the_typed_classifier_is_backed_by_a_marker():
    """`Typing :: Typed` advertises that the inline annotations are usable, but
    only the marker file makes that true -- PEP 561 requires it, and without it
    a checker treats every import as `Any`."""
    if "Typing :: Typed" in PROJECT["classifiers"]:
        assert (ROOT / "src" / "sqlalign" / "py.typed").exists(), (
            "the Typing :: Typed classifier promises types a checker cannot see; "
            "add src/sqlalign/py.typed or drop the classifier")


def test_the_changelog_documents_the_version_being_shipped():
    """A release whose notes are one version behind is one nobody can read the
    notes for. The release workflow checks the tag against this version, so this
    is the half of the agreement that CI cannot see."""
    versions = [line[3:].strip() for line in (ROOT / "CHANGELOG.md").read_text().splitlines()
                if line.startswith("## ")]
    assert PROJECT["version"] in versions, (
        f"pyproject is {PROJECT['version']}, and CHANGELOG.md documents {versions[:3]}")
    assert versions[0] == PROJECT["version"], (
        f"CHANGELOG.md leads with {versions[0]}, not the version being shipped")


def test_the_python_classifiers_match_requires_python():
    """A classifier nobody runs is a guess. These are what a user filters PyPI
    on, and CI runs exactly the versions named here."""
    claimed = {c.rsplit(" :: ", 1)[1] for c in PROJECT["classifiers"]
               if c.startswith("Programming Language :: Python :: 3.")}
    assert claimed == {"3.12", "3.13"}, claimed
    assert PROJECT["requires-python"] == ">=" + min(claimed)


def test_every_project_url_is_absolute_and_https():
    """These become the sidebar links on PyPI, where a relative path is dead."""
    for name, url in PROJECT["urls"].items():
        assert url.startswith("https://"), f"{name} is {url}"


def test_the_lint_extra_is_the_only_extra_and_sqlfluff_is_not_required():
    """sqlalign's guarantee does not depend on a linter. sqlfluff staying
    optional is what keeps the install small and the dependency surface honest.
    """
    assert set(PROJECT["optional-dependencies"]) == {"lint"}
    assert not any("sqlfluff" in d for d in PROJECT["dependencies"])


def test_no_scratch_files_are_tracked_at_the_repository_root():
    """Everything at the root ships in the sdist, so a stray file a debugging
    session left behind becomes part of a release. `victim.sql` and
    `victim2.sql` reached a merged commit exactly this way.

    SQL belongs in `samples/` or `tests/fixtures/`; the root holds packaging and
    prose only.
    """
    allowed_suffixes = {".md", ".toml", ".lock", ".sqlfluff", ""}
    strays = [p.name for p in ROOT.iterdir()
              if p.is_file() and not p.name.startswith(".")
              and p.suffix not in allowed_suffixes]
    assert not strays, f"unexpected files at the repository root: {strays}"
