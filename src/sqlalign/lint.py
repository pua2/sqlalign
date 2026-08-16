"""`--lint`: run sqlfluff over the formatted result, in the same command.

Formatting and linting are separate jobs and this does not merge them: it just
saves running two tools over the same files. What it adds is the guarantee that
the two are not fighting: the linter never sees the layout sqlalign has just
produced as a finding, because it runs under the coexistence config
(`sqlfluffconfig.py`) unless the team has committed one of their own.

sqlfluff is an OPTIONAL dependency (`pip install 'sqlalign[lint]'`), imported
only when this runs. sqlalign's own guarantee does not depend on a linter, and
neither should its install.

Config resolution deliberately prefers the team's file. A committed `.sqlfluff`
is a decision someone made; silently overriding it with a generated one would be
the wrong kind of helpful. The generated config is the fallback for when there
is nothing to respect, and it says so on stderr the first time.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalign import bodylint
from sqlalign.sqlfluffconfig import TESTED_SQLFLUFF, sqlfluff_config

# sqlfluff config sources, in the order it searches them (walking up from the
# file). If any exists, it is the team's decision and we stay out of the way.
_CONFIG_NAMES = (".sqlfluff", "pyproject.toml", "setup.cfg", "tox.ini", ".sqlfluffignore")


class LintUnavailable(RuntimeError):
    """sqlfluff is not installed. Actionable rather than a traceback."""


def sqlfluff_version() -> str:
    try:
        import sqlfluff
    except ImportError as e:                       # pragma: no cover - env-dependent
        raise LintUnavailable(
            "--lint needs sqlfluff, which is an optional dependency. "
            "Install it with: pip install 'sqlalign[lint]'") from e
    return sqlfluff.__version__


def discovered_config(path: Path) -> Path | None:
    """The sqlfluff config that applies to `path`, or None if there is none.

    Only `.sqlfluff` is treated as decisive on sight; the shared files
    (pyproject.toml, setup.cfg, tox.ini) count only when they actually carry a
    sqlfluff section, since almost every Python project has a pyproject.
    """
    for parent in [path.parent, *path.parent.parents]:
        for name in _CONFIG_NAMES:
            candidate = parent / name
            if not candidate.is_file():
                continue
            if name == ".sqlfluff" or name == ".sqlfluffignore":
                return candidate
            try:
                if "sqlfluff" in candidate.read_text():
                    return candidate
            except OSError:
                continue
    return None


def lint(path: Path, text: str, style, dialect: str) -> tuple[int, str, str]:
    """Lint `text` as though it were `path`. Returns (returncode, stdout, stderr).

    The text is linted rather than the file, so `--check` and `--stdout` lint
    what would be written instead of what is currently on disk. It goes into a
    temporary file beside the original so sqlfluff's own config discovery still
    walks the real directory tree.
    """
    config = discovered_config(path)
    args = ["lint"]
    generated = None
    if config is None:
        # Nothing to respect: fall back to the config that keeps the two tools
        # from contradicting each other.
        generated = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed below
            "w", suffix=".sqlfluff", delete=False)
        generated.write(sqlfluff_config(style, dialect))
        generated.close()
        args += ["--config", generated.name]

    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".sql",
                                         prefix=f".{path.stem}.sqlalign-", delete=False) as f:
            f.write(text)
            tmp = Path(f.name)
        result = subprocess.run([sys.executable, "-m", "sqlfluff", *args, str(tmp)],
                                capture_output=True, text=True)
        code, out, err = result.returncode, result.stdout, result.stderr

        # sqlfluff cannot see inside a `$$` body: to its parser the whole
        # body is one string literal. sqlalign already locates those statements
        # in order to format them, so it can lint them too: `bodylint.lint_view`
        # is the file with the plpgsql scaffolding blanked and every line and
        # column left where it was, so a finding's position is a position in
        # the real file.
        body = _lint_bodies(text, args, path, dialect)
        if body:
            out += body
            code = code or 1

        # Report against the real filename, never the scratch one.
        return (code,
                out.replace(str(tmp), str(path)).replace(tmp.name, path.name),
                err.replace(str(tmp), str(path)).replace(tmp.name, path.name))
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        if generated is not None:
            Path(generated.name).unlink(missing_ok=True)


def _lint_bodies(text: str, args: list[str], path: Path, dialect: str) -> str:
    """Findings from inside `$$` bodies, rendered in sqlfluff's own line format.

    Kept separate from the main run rather than merged into it. The two cover
    disjoint regions -- a body's contents are invisible to the ordinary run --
    so nothing is reported twice, and labelling the section says plainly that
    these are findings no linter would have produced on its own.
    """
    if not bodylint.has_bodies(text, dialect):
        return ""
    wanted = bodylint.body_lines(text, dialect)
    if not wanted:
        return ""

    view = bodylint.lint_view(text, dialect)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".sql",
                                         prefix=f".{path.stem}.sqlalign-body-",
                                         delete=False) as f:
            f.write(view)
            tmp = Path(f.name)
        result = subprocess.run(
            [sys.executable, "-m", "sqlfluff", *args, "--format", "json", str(tmp)],
            capture_output=True, text=True)
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:            # sqlfluff failed before linting
            return ""
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)

    found = [v for entry in payload for v in entry.get("violations", [])
             if v.get("start_line_no") in wanted]
    if not found:
        return ""
    found.sort(key=lambda v: (v["start_line_no"], v["start_line_pos"]))
    lines = [f"== [{path}] inside $$ bodies "
             "(sqlfluff cannot reach these on its own)"]
    lines += [f"L: {v['start_line_no']:>3} | P: {v['start_line_pos']:>3} | "
              f"{v['code']} | {v['description']} [{v['name']}]" for v in found]
    return "\n".join(lines) + "\n"


def _series(version: str) -> tuple[int, int] | None:
    """`(major, minor)` from a version string, or None if it does not parse."""
    try:
        major, minor = version.split(".")[:2]
        return int(major), int(minor)
    except ValueError:
        return None


def version_warning() -> str | None:
    """A note when the installed sqlfluff is NEWER than the release the mappings
    were checked against.

    Only the series is compared. A patch release does not add or rename a rule,
    so warning on one would fire for every user in the window between a sqlfluff
    point release and the next sqlalign — noise that says nothing about their
    config. An OLDER sqlfluff is silent too: the floor on the `lint` extra is
    what bounds that, and it is verified.

    Anything this cannot positively rule out warns, since the cost of the note
    is far below the cost of a config whose rules have quietly moved.
    """
    installed = sqlfluff_version()
    here, checked = _series(installed), _series(TESTED_SQLFLUFF)
    if here is not None and checked is not None and here <= checked:
        return None
    return (f"sqlfluff {installed} installed, coexistence config was checked "
            f"against {TESTED_SQLFLUFF} — rules may have moved")
