"""Config-file discovery, parsing, and precedence.

Every style knob was CLI-only until now, which meant a team could not commit its
own style: the whole point of having the knobs. Settings come from, in
increasing precedence:

    built-in house defaults  <  config file  <  command-line flags

A config file is either a `.sqlalign.toml` (keys at top level) or a
`pyproject.toml` carrying a `[tool.sqlalign]` table. Discovery walks up from the
directory of the file being formatted and takes the first of either, so a repo
can hold one config at its root and a subdirectory can override it.

Unknown keys are a hard ERROR rather than a silent no-op: a typo'd key in a
committed config would otherwise mean a team believes it has a setting it does
not have, and would only discover it from a surprising diff. `--no-strict-config`
downgrades that to a warning for the case where a config is shared with a newer
sqlalign that knows keys this one does not.
"""
try:
    import tomllib
except ModuleNotFoundError:            # Python 3.10
    import tomli as tomllib
from pathlib import Path

from sqlalign.config import Width
from sqlalign.style import PRESETS, SETTING_SUMMARIES, Style

FILENAME = ".sqlalign.toml"
PYPROJECT = "pyproject.toml"

# Keys accepted in a config file, mapped to how they are read. `width` becomes a
# Width; `align_targets` becomes a frozenset. Everything else is passed through
# to Style, which validates the VALUE (this module only validates the KEY).
_SCALAR_KEYS = frozenset({
    "align", "format_dollar_bodies", "neq_style", "decimal_style",
    "comma_position", "boolean_operator_position", "on_placement",
    "protect_templating", "table_alias_style",
    "select_placement", "select_indent",
    "clause_keyword_align", "river_gutter", "body_blank_lines",
    "blank_lines_between_statements",
    "keyword_case",
})
KNOWN_KEYS = _SCALAR_KEYS | {"width", "align_targets", "preset", "exclude",
                            "blank_lines_between_statements"}

# `exclude` selects FILES rather than style, so it is read separately and never
# reaches Style. Kept in KNOWN_KEYS so a config carrying it is still valid.
_NON_STYLE_KEYS = frozenset({"exclude"})


def load_excludes(path: Path, *, strict: bool = True) -> list[str]:
    """The `exclude` glob patterns from a config file, if any."""
    settings, _ = load_settings(path, strict=strict)
    patterns = settings.get("exclude", [])
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ConfigError(f"{path}: exclude must be a list of glob patterns")
    return patterns


class ConfigError(ValueError):
    """A config file exists but cannot be used as written."""


def find_config(start: Path) -> Path | None:
    """First `.sqlalign.toml`, or `pyproject.toml` with a `[tool.sqlalign]`
    table, at or above `start`'s directory. None if there is none."""
    start = start.resolve()
    directory = start if start.is_dir() else start.parent
    for folder in [directory, *directory.parents]:
        candidate = folder / FILENAME
        if candidate.is_file():
            return candidate
        pyproject = folder / PYPROJECT
        if pyproject.is_file() and _pyproject_table(pyproject) is not None:
            return pyproject
    return None


def _pyproject_table(path: Path):
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh).get("tool", {}).get("sqlalign")
    except (OSError, tomllib.TOMLDecodeError):
        return None


def load_settings(path: Path, *, strict: bool = True) -> tuple[dict, list[str]]:
    """Read `path` into a settings dict. Returns `(settings, warnings)`."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as e:
        raise ConfigError(f"{path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from e

    if path.name == PYPROJECT:
        data = data.get("tool", {}).get("sqlalign", {})

    warnings = []
    unknown = sorted(set(data) - KNOWN_KEYS)
    if unknown:
        message = (f"{path}: unknown setting(s) {unknown}; "
                   f"valid: {sorted(KNOWN_KEYS)}")
        if strict:
            raise ConfigError(message)
        warnings.append(message)
    return {k: v for k, v in data.items() if k in KNOWN_KEYS}, warnings


def build_style(settings: dict, overrides: dict | None = None) -> Style:
    """Compose a Style from file settings plus command-line overrides, with the
    overrides winning. Type errors are reported against the key that caused
    them, so a bad config says which line to fix."""
    merged = {**settings, **{k: v for k, v in (overrides or {}).items() if v is not None}}

    # A preset supplies the BASE, which explicit keys then layer over: so
    # `preset = "compact"` plus `comma_position = "trailing"` means both, not one
    # or the other.
    for key in _NON_STYLE_KEYS:                # file selection, not style
        merged.pop(key, None)
    preset = merged.pop("preset", None)
    if preset is not None:
        if preset not in PRESETS:
            raise ConfigError(f"unknown preset {preset!r}; valid: {sorted(PRESETS)}")
        merged = {**PRESETS[preset], **merged}

    kwargs = {}
    if "width" in merged:
        if not isinstance(merged["width"], int) or isinstance(merged["width"], bool):
            raise ConfigError(f"width must be an integer, got {merged['width']!r}")
        kwargs["width"] = Width(width=merged["width"])
    if "align_targets" in merged:
        targets = merged["align_targets"]
        if isinstance(targets, str):                      # CLI passes a list already
            targets = [t.strip() for t in targets.split(",") if t.strip()]
        if not isinstance(targets, (list, tuple, frozenset, set)):
            raise ConfigError(f"align_targets must be a list, got {targets!r}")
        kwargs["align_targets"] = frozenset(targets)
    for key in _SCALAR_KEYS:
        if key in merged:
            kwargs[key] = merged[key]

    try:
        return Style(**kwargs)
    except ValueError as e:                                # Style validates values
        raise ConfigError(str(e)) from e


def resolve(path: Path, overrides: dict | None = None, *,
            explicit: Path | None = None, isolated: bool = False,
            strict: bool = True) -> tuple[Style, Path | None, list[str]]:
    """The full resolution for one input file: discover (unless `isolated` or an
    `explicit` config is given), load, merge, validate. Returns
    `(style, config_path_used, warnings)`."""
    if isolated:
        return build_style({}, overrides), None, []
    config_path = explicit if explicit is not None else find_config(path)
    if config_path is None:
        return build_style({}, overrides), None, []
    settings, warnings = load_settings(config_path, strict=strict)
    return build_style(settings, overrides), config_path, warnings


def starter(style: Style, preset: str | None = None) -> str:
    """A commented `.sqlalign.toml` for a project that has none.

    Every setting is emitted COMMENTED OUT, showing the value currently in
    effect. The file therefore changes nothing until something is uncommented,
    which is the property that makes it safe to write into a repository: a
    starter that pinned eighteen settings would freeze a team on today's
    defaults and call it a choice.

    A `--preset` is the exception and is written live, because choosing one is
    the decision the reader just made. The settings below it then show what that
    preset does, so the file doubles as the answer to "what did I just pick".

    Values come from `describe`, so what a starter shows and what
    `--show-config` reports cannot drift apart.
    """
    rendered = {}
    for line in describe(style).splitlines():
        if line.startswith("#") or " = " not in line:
            continue
        rendered[line.split(" = ", 1)[0]] = line

    out = [
        "# sqlalign configuration.",
        "#",
        "# Written by `sqlalign --init`. Every setting is commented out and shows the",
        "# value currently in effect, so this file changes nothing until you uncomment",
        "# something.",
        "#",
        "# Reference: https://sqlalign.lumaru.app/v1/settings.html",
        "",
    ]
    if preset:
        out += [
            '# The base every setting below starts from. Flags and any setting you',
            '# uncomment layer on top of it.',
            f'preset = "{preset}"',
            "",
        ]
    for name, summary in SETTING_SUMMARIES.items():
        line = rendered.get(name)
        out.append(f"# {summary}")
        if line is None:
            # `blank_lines_between_statements` has no TOML spelling when unset.
            out.append(f"# {name} =")
        else:
            out.append(f"# {line}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def describe(style: Style) -> str:
    """The effective settings, as the TOML a user could paste into a config."""
    lines = [
        f"width = {style.width.width}",
        f"align = {str(style.align).lower()}",
        "align_targets = [" + ", ".join(f'"{t}"' for t in sorted(style.align_targets)) + "]",
        f'comma_position = "{style.comma_position}"',
        f'boolean_operator_position = "{style.boolean_operator_position}"',
        f'on_placement = "{style.on_placement}"',
        f'select_placement = "{style.select_placement}"',
        f"select_indent = {style.select_indent}",
        f'clause_keyword_align = "{style.clause_keyword_align}"',
        f"river_gutter = {style.river_gutter}",
        f"body_blank_lines = {style.body_blank_lines}",
        f"format_dollar_bodies = {str(style.format_dollar_bodies).lower()}",
        f'neq_style = "{style.neq_style}"',
        f'decimal_style = "{style.decimal_style}"',
        f'table_alias_style = "{style.table_alias_style}"',
        f'keyword_case = "{style.keyword_case}"',
        f"protect_templating = {str(style.protect_templating).lower()}",
        # An unset value has no TOML spelling, so it is emitted COMMENTED OUT
        # rather than as `= # unset`, which is not valid TOML: the output of
        # --show-config has to be loadable as a config, and a round-trip test
        # asserts exactly that.
        (f"blank_lines_between_statements = {style.blank_lines_between_statements}"
         if style.blank_lines_between_statements is not None else
         "# blank_lines_between_statements is unset: one blank line between two\n"
         "# multi-line statements, none otherwise. Set an integer to force a count."),
    ]
    return "\n".join(lines)
