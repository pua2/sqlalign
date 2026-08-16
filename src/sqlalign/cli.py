import argparse
import difflib
import sys
from fnmatch import fnmatch
from pathlib import Path

from sqlalign import configfile
from sqlalign.formatter import format_sql
from sqlalign.lint import LintUnavailable, lint, version_warning
from sqlalign.sqlfluffconfig import sqlfluff_config
from sqlalign.style import PRESETS, SUPPORTED_DIALECTS, Style


def _is_excluded(path: Path, root: Path, patterns: list[str]) -> bool:
    """Whether `path` matches any exclude glob, tested against its path relative
    to the directory being expanded (posix separators, `fnmatch` semantics)."""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return any(fnmatch(rel, pat) or fnmatch(path.name, pat) for pat in patterns)


def _expand(names, cli_excludes: list[str], isolated: bool,
            explicit_config: Path | None) -> list[Path]:
    """Turn the command-line arguments into the list of files to format.

    A directory expands to its `*.sql` files, recursively and in sorted order so
    a run is reproducible. Exclude patterns come from `--exclude` plus the
    `exclude` key of the config discovered at that directory: file selection is
    resolved per ROOT, before the per-file style resolution, because which files
    to touch cannot sensibly vary file by file.

    A file named explicitly on the command line is never excluded: asking for it
    by name is a clearer signal of intent than a pattern in a config.
    """
    out: list[Path] = []
    for name in names:
        path = Path(name)
        if not path.is_dir():
            out.append(path)
            continue
        patterns = list(cli_excludes)
        if not isolated:
            config = explicit_config or configfile.find_config(path)
            if config is not None:
                patterns += configfile.load_excludes(config)
        out.extend(f for f in sorted(path.rglob("*.sql"))
                   if not _is_excluded(f, path, patterns))
    return out


def _report(seen: int, declined: int, causes: dict[tuple[str, str], int]) -> str:
    """The coverage summary.

    A passthrough is safe but INVISIBLE: it warns on stderr and exits 0, so a CI
    run stays green with any fraction of a repository unformatted. Counting them
    is what turns "sqlalign formatted your SQL" into a number, and turns the
    remaining gaps into a ranked list of what to implement next: measured on
    the team's own SQL rather than guessed at.
    """
    formatted = seen - declined
    pct = f"{formatted / seen * 100:.1f}%" if seen else "n/a"
    lines = [f"  {seen:,} statements   {formatted:,} formatted ({pct})   "
             f"{declined:,} declined"]
    if causes:
        lines += ["", "  declined by cause"]
        width = max(len(kind) for kind, _ in causes)
        for (kind, reason), count in sorted(causes.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"    {count:>4}  {kind:<{width}}  {reason}")
    return "\n".join(lines)


def _version() -> str:
    """The installed version, or a marker when running from a source tree that
    was never installed."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("sqlalign")
    except PackageNotFoundError:          # running from a checkout
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    """The CLI parser. Split out of `main` so the docs generator and the
    tests can introspect it without running a command."""
    p = argparse.ArgumentParser(prog="sqlalign")
    p.add_argument("--version", action="version", version=f"sqlalign {_version()}",
                   help="print the version and exit")
    # `nargs="*"` rather than "+" so `--print-sqlfluff-config` can run with no
    # file at all; every other mode still requires one (checked below).
    p.add_argument("files", nargs="*", help="files or directories (a directory is "
                   "searched recursively for *.sql)")
    output_mode = p.add_mutually_exclusive_group()
    output_mode.add_argument("--check", action="store_true",
                             help="do not write; exit 1 if any file would change")
    output_mode.add_argument("--stdout", action="store_true",
                             help="write the result to stdout instead of the file")
    output_mode.add_argument("--diff", action="store_true",
                             help="write nothing; print a unified diff of what would "
                                  "change (exit 1 if anything would)")
    p.add_argument("--dialect", choices=sorted(SUPPORTED_DIALECTS), default="postgres",
                   help="the SQL dialect to parse and print (default: postgres)")
    p.add_argument("--line-ending", choices=["auto", "lf", "crlf"], default="auto",
                   help="line endings to write: auto preserves the file's own (default)")
    # Config-file plumbing.
    p.add_argument("--config", type=Path,
                   help="use this config file instead of discovering one")
    p.add_argument("--exclude", action="append", metavar="GLOB",
                   help="skip files matching this glob when expanding a directory "
                        "(repeatable; also settable as `exclude` in a config file)")
    p.add_argument("--isolated", action="store_true",
                   help="ignore any config file and use the built-in defaults")
    p.add_argument("--show-config", action="store_true",
                   help="print the effective settings as TOML and exit")
    p.add_argument("--gui", action="store_true",
                   help="(experimental) open the settings panel with a live preview, and exit")
    p.add_argument("--report", action="store_true",
                   help="print a coverage summary: how many statements were "
                        "formatted, and what the rest declined on. Adds output "
                        "without changing the mode, so on its own it still "
                        "rewrites; pair it with `--check` to survey without "
                        "writing")
    p.add_argument("--max-declines", type=int, metavar="N",
                   help="exit 1 if more than N statements are passed through "
                        "unformatted (implies --report)")
    p.add_argument("--lint", action="store_true",
                   help="after formatting, run sqlfluff over the result "
                        "(needs the optional `sqlalign[lint]` extra)")
    p.add_argument("--print-sqlfluff-config", action="store_true",
                   help="print a .sqlfluff that lets sqlfluff run alongside "
                        "sqlalign without fighting it, and exit")
    p.add_argument("--no-strict-config", action="store_true",
                   help="warn on unknown config keys instead of failing")
    p.add_argument("--preset", choices=sorted(PRESETS),
                   help="named starting point; config keys and flags layer on top")
    # Style knobs. Every one defaults to None so "not passed" is distinguishable
    # from "passed the same value as the default": otherwise a config file
    # could never be overridden, or (worse) could never take effect at all.
    p.add_argument("--width", type=int,
                   help="column the formatter tries to stay inside; 0 turns it off")
    p.add_argument("--blank-lines-between-statements", type=int, metavar="N",
                   help="force N blank lines between every pair of statements "
                        "(default: one only between two multi-line statements)")
    p.add_argument("--no-align", action="store_const", const=False, dest="align",
                   help="emit one space between tokens instead of aligning them into "
                        "columns (same line structure, no padding)")
    p.add_argument("--no-protect-templating", action="store_const", const=False,
                   dest="protect_templating",
                   help="do not mask Jinja/dbt template expressions before formatting")
    p.add_argument("--no-format-bodies", action="store_const", const=False,
                   dest="format_dollar_bodies",
                   help="leave dollar-quoted ($$) procedure and function bodies untouched")
    p.add_argument("--align-targets",
                   help="comma-separated alignment targets to keep. Default is every "
                        "target except table_names, which is opt-in because it pads "
                        "the FROM/JOIN keyword out to a shared table column. "
                        "Valid: aliases, table_names, operators, join_conditions, "
                        "case_results, column_types, column_constraints")
    p.add_argument("--comma-position", choices=["leading", "trailing"],
                   help="where the separator comma sits in a stacked list (default: leading)")
    p.add_argument("--boolean-operator-position", choices=["leading", "trailing"],
                   help="where AND/OR sit when a predicate spans lines (default: leading)")
    p.add_argument("--on-placement", choices=["inline", "own_line"],
                   help="whether a JOIN ON rides the table line or drops below it")
    p.add_argument("--select-placement", choices=["inline", "own_line"],
                   help="whether the first select item rides the SELECT line")
    p.add_argument("--select-indent", type=int, metavar="N",
                   help="columns the select list indents under "
                        "--select-placement own_line (default: 2)")
    p.add_argument("--body-blank-lines", type=int, metavar="N",
                   help="blank lines between the elements of a $$ body (default: 1)")
    p.add_argument("--clause-keyword-align", choices=["left", "river"],
                   help="root clause keywords flush left, or right-aligned to a river")
    p.add_argument("--river-gutter", type=int, metavar="N",
                   help="column a river right-aligns clause keywords to (default: 6)")
    p.add_argument("--keyword-case", choices=["upper", "lower"],
                   help="case for keywords, function names and types (default: upper)")
    p.add_argument("--neq-style", choices=["!=", "<>"],
                   help="spelling for the not-equal operator (default: !=)")
    p.add_argument("--table-alias-style", choices=["bare", "as"],
                   help="print a table alias as `t a` (bare) or `t AS a`")
    p.add_argument("--decimal-style", choices=["NUMERIC", "DECIMAL"],
                   help="spelling for the NUMERIC/DECIMAL type (default: NUMERIC)")
    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)

    overrides = {
        "preset": args.preset,
        "width": args.width,
        "blank_lines_between_statements": args.blank_lines_between_statements,
        "align": args.align,
        "align_targets": args.align_targets,
        "format_dollar_bodies": args.format_dollar_bodies,
        "protect_templating": args.protect_templating,
        "neq_style": args.neq_style,
        "decimal_style": args.decimal_style,
        "table_alias_style": args.table_alias_style,
        "select_placement": args.select_placement,
        "select_indent": args.select_indent,
        "body_blank_lines": args.body_blank_lines,
        "clause_keyword_align": args.clause_keyword_align,
        "river_gutter": args.river_gutter,
        "keyword_case": args.keyword_case,
        "comma_position": args.comma_position,
        "boolean_operator_position": args.boolean_operator_position,
        "on_placement": args.on_placement,
    }

    def style_for(path: Path) -> Style:
        """Settings for one input file: house defaults < config file < flags.
        Resolved per file so one invocation can span repos with different
        configs."""
        style, _source, warns = configfile.resolve(
            path, overrides, explicit=args.config, isolated=args.isolated,
            strict=not args.no_strict_config)
        for w in warns:
            print(f"sqlalign: {w}", file=sys.stderr)
        return style

    if args.gui:
        from sqlalign.gui import run
        return run(args.dialect)

    if not args.files and not (args.print_sqlfluff_config or args.gui):
        p.error("the following arguments are required: files")

    if args.print_sqlfluff_config:
        # Resolved against the first file like --show-config, so the generated
        # config reflects the style that actually applies there rather than the
        # built-in defaults. With no file given, the defaults are the honest
        # answer: there is no directory to discover a config from.
        try:
            style = (configfile.resolve(
                Path(args.files[0]), overrides, explicit=args.config,
                isolated=args.isolated, strict=not args.no_strict_config)[0]
                if args.files else configfile.build_style({}, overrides))
        except configfile.ConfigError as e:
            p.error(str(e))
        print(sqlfluff_config(style, args.dialect), end="")
        return 0

    if args.show_config:
        try:
            style, source, warns = configfile.resolve(
                Path(args.files[0]), overrides, explicit=args.config,
                isolated=args.isolated, strict=not args.no_strict_config)
        except configfile.ConfigError as e:
            p.error(str(e))
        for w in warns:
            print(f"sqlalign: {w}", file=sys.stderr)
        print(f"# {source}" if source else "# built-in defaults (no config file found)")
        print(configfile.describe(style))
        return 0

    try:
        targets = _expand(args.files, args.exclude or [], args.isolated, args.config)
    except configfile.ConfigError as e:
        p.error(str(e))

    if args.lint:
        try:
            warning = version_warning()
        except LintUnavailable as e:
            print(f"sqlalign: {e}", file=sys.stderr)
            return 2
        if warning:
            print(f"sqlalign: {warning}", file=sys.stderr)

    seen = declined = 0
    causes: dict[tuple[str, str], int] = {}

    rc = 0
    for path in targets:
        name = str(path)
        try:
            # newline="" disables universal-newline translation so CRLF/CR bytes
            # survive into `original` and can be detected below (v1 is LF-only).
            with path.open(newline="") as f:
                original = f.read()
        except OSError as e:
            print(f"sqlalign: {e}", file=sys.stderr)
            rc = 2
            continue
        try:
            style = style_for(path)
        except configfile.ConfigError as e:   # a broken config must not be guessed past
            print(f"sqlalign: {e}", file=sys.stderr)
            rc = 2
            continue
        # CRLF files are formatted normally: normalize to LF for the engine, then
        # restore the file's own ending on the way out (--line-ending overrides).
        # A LONE "\r" (classic-Mac) is not a line ending this tool models, so those
        # files still pass through untouched rather than being silently rewritten.
        source_ending = "crlf" if "\r\n" in original else "lf"
        normalized = original.replace("\r\n", "\n")
        if "\r" in normalized:
            print(f"sqlalign: {name}: lone CR line endings — passed through untouched",
                  file=sys.stderr)
            if args.stdout:
                sys.stdout.write(original)
            continue
        try:
            result = format_sql(normalized, args.dialect, style)
        except Exception as e:  # parse/safety failures surface as exit 2, file untouched
            print(f"sqlalign: {name}: {e}", file=sys.stderr)
            rc = 2
            continue
        seen += result.statements
        declined += len(result.declines)
        for d in result.declines:
            causes[(d.kind, d.reason)] = causes.get((d.kind, d.reason), 0) + 1
        for w in result.warnings:
            print(f"sqlalign: {name}: {w}", file=sys.stderr)
        # Restore line endings before comparing or writing, so a CRLF file that is
        # already formatted compares equal (and --check does not report a spurious
        # diff on every line) instead of differing purely by "\r".
        target_ending = source_ending if args.line_ending == "auto" else args.line_ending
        formatted = result.text.replace("\n", "\r\n") if target_ending == "crlf" else result.text
        if args.stdout:
            sys.stdout.write(formatted)
        elif args.check or args.diff:
            # Both write nothing and report through the exit code; they differ in
            # what they print. --check names the files that would change (enough
            # for CI to be actionable without burying the log in diffs); --diff
            # shows the change itself.
            if formatted != original:
                if args.diff:
                    sys.stdout.writelines(difflib.unified_diff(
                        original.splitlines(keepends=True),
                        formatted.splitlines(keepends=True),
                        fromfile=name, tofile=f"{name} (formatted)"))
                else:
                    print(f"would reformat {name}")
                rc = max(rc, 1)
        elif formatted != original:
            with open(path, "w", newline="") as f:
                f.write(formatted)

        if args.lint:
            # Lint what would be written, so --check and --stdout report on the
            # formatted result rather than on whatever is currently on disk.
            code, out, err = lint(path, formatted, style, args.dialect)
            sys.stdout.write(out)
            sys.stderr.write(err)
            if code:
                rc = max(rc, 1)

    if args.report or args.max_declines is not None:
        print(_report(seen, declined, causes))
    if args.max_declines is not None and declined > args.max_declines:
        print(f"sqlalign: {declined} declined, over the --max-declines limit of "
              f"{args.max_declines}", file=sys.stderr)
        rc = max(rc, 1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
