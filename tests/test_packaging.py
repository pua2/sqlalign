"""What a user gets from `pip install sqlalign`, as opposed to what the source
tree does.

Nothing else in the suite looks at packaging metadata, so it is the one surface
where a claim can be wrong for a whole release without a single test noticing.
`1.0.1` shipped the `Typing :: Typed` classifier with no `py.typed` beside it,
which meant every type checker skipped the package while the metadata said the
opposite.
"""
import pathlib

import pytest

try:
    import tomllib
except ModuleNotFoundError:            # Python 3.10
    import tomli as tomllib

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
    headings = [line[3:].strip() for line in (ROOT / "CHANGELOG.md").read_text().splitlines()
                if line.startswith("## ")]
    # An `Unreleased` section is where changes accumulate between releases, so it
    # is allowed to sit on top. What must hold is that the version being shipped
    # is the first RELEASED heading under it -- a changelog one version behind is
    # one nobody can read the notes for.
    released = [h for h in headings if h.lower() != "unreleased"]
    assert PROJECT["version"] in released, (
        f"pyproject is {PROJECT['version']}, and CHANGELOG.md documents {released[:3]}")
    assert released[0] == PROJECT["version"], (
        f"CHANGELOG.md's newest release is {released[0]}, not the version being shipped")


def _as_version(text: str) -> tuple[int, ...]:
    """`"3.10"` sorts above `"3.9"` numerically and below it as a string."""
    return tuple(int(part) for part in text.split("."))


def test_the_python_classifiers_match_requires_python():
    """Three things have to agree, and nothing but this test makes them.

    A classifier is what a user filters PyPI on, `requires-python` is what pip
    enforces, and the CI matrix is the only evidence any of it is true. A
    classifier for a version no job runs is a guess; a `requires-python` floor
    below the lowest classifier is a promise nobody checked.
    """
    import re

    claimed = {c.rsplit(" :: ", 1)[1] for c in PROJECT["classifiers"]
               if c.startswith("Programming Language :: Python :: 3.")}
    assert claimed, "no Python version classifiers"

    floor = PROJECT["requires-python"].removeprefix(">=")
    assert min(claimed, key=_as_version) == floor, (
        f"classifiers start at {min(claimed, key=_as_version)}, "
        f"requires-python at {floor}")

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    matrix = set(re.findall(r'"(3\.\d+)"',
                            re.search(r"python: \[(.*?)\]", workflow).group(1)))
    assert matrix == claimed, (
        f"CI runs {sorted(matrix, key=_as_version)}, "
        f"classifiers claim {sorted(claimed, key=_as_version)}")


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
    # GitHub resolves `uses: pua2/sqlalign@v1` to an action manifest at the
    # repository root, so this one cannot live anywhere tidier.
    allowed_names = {"action.yml"}
    strays = [p.name for p in ROOT.iterdir()
              if p.is_file() and not p.name.startswith(".")
              and p.name not in allowed_names
              and p.suffix not in allowed_suffixes]
    assert not strays, f"unexpected files at the repository root: {strays}"


def test_ci_tests_the_newest_sqlglot_the_range_allows():
    """The dependency range and the CI job that covers its far end have to name
    the same range.

    The locked version is the floor and the matrix covers it. The ceiling is
    covered by one job, and if its constraint drifts from pyproject's the range
    widens with nothing testing the new part -- which is exactly the failure the
    job exists to prevent, arriving silently.
    """
    import re

    spec = next(d for d in PROJECT["dependencies"] if d.startswith("sqlglot"))
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    installed = re.search(r"uv pip install --upgrade '([^']+)'", workflow)
    assert installed, "no job installs a newer sqlglot"
    assert installed.group(1) == spec, (
        f"pyproject allows {spec!r}, CI tests {installed.group(1)!r}")


def test_the_sdist_carries_no_internal_files():
    """The sdist include list is an allowlist because hatchling's default is
    the working tree minus .gitignore -- and only .gitignore: a directory
    ignored via .git/info/exclude, or tracked for internal use, shipped in any
    build made from a tree that carried one. This builds the real sdist and
    walks it, so the guarantee is about the artifact rather than the config.
    """
    import shutil
    import subprocess
    import tarfile
    import tempfile

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH to build the sdist")

    with tempfile.TemporaryDirectory() as tmp:
        build = subprocess.run([uv, "build", "--sdist", "-o", tmp],
                               capture_output=True, text=True, cwd=ROOT)
        assert build.returncode == 0, build.stderr
        archive = next(pathlib.Path(tmp).glob("*.tar.gz"))
        with tarfile.open(archive) as tar:
            names = tar.getnames()

    # Structural rather than a blocklist: a blocklist names the things it is
    # keeping out, which republishes them in every sdist of this file. Any
    # hidden directory beyond .github, anything under docs/ beyond the three
    # published entries, and any top-level directory outside the allowlist is
    # a leak, whatever it is called.
    allowed_top = {"src", "tests", "tools", "scripts", "samples", "docs",
                   ".github", "README.md", "CHANGELOG.md", "CONTRIBUTING.md",
                   "LICENSE", ".sqlfluff", ".gitignore", ".pre-commit-hooks.yaml",
                   "action.yml", "uv.lock", "pyproject.toml", "PKG-INFO"}
    allowed_docs = ("docs/guide/", "docs/v1/", "docs/index.html")
    leaked = []
    for name in names:
        parts = name.split("/", 2)          # sdist paths start "sqlalign-X.Y.Z/"
        if len(parts) < 2 or not parts[1]:
            continue
        top = parts[1]
        if top not in allowed_top:
            leaked.append(name)
        elif top == "docs" and len(parts) > 2:
            relative = "docs/" + parts[2]
            if not relative.startswith(allowed_docs) and relative != "docs/index.html":
                leaked.append(name)
    assert not leaked, leaked
    # The allowlist must not starve the suite either: the files a downstream
    # needs to run the tests are part of the artifact's contract.
    for needed in ("tests/corpus/README.md", "tests/fixtures/expected/01.sql",
                   "samples/queries.sql", "docs/guide/faq.md", "uv.lock"):
        assert any(n.endswith(needed) for n in names), f"{needed} missing from sdist"
