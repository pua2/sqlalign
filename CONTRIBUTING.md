# Contributing

Thanks for looking. This file covers the three things about this repository that
are not obvious from the code, and that will otherwise cost you an afternoon.

## Setup

```sh
uv sync                 # Python 3.10+
uv run pytest -q        # ~4,000 tests, about a minute
uv run ruff check .
```

Both must be clean before a pull request. CI runs the same two checks on Python
3.10 through 3.13, and then five more jobs, each separate so a red build says
which thing broke:

| Job | What it protects |
|---|---|
| `goldens` | the style, byte for byte — this is what makes the stability policy true |
| `docs` | the committed site still matches its generator |
| `action` | `action.yml` is a published interface; nothing else runs it |
| `sqlfluff-floor` | the oldest sqlfluff the `lint` extra allows |
| `sqlglot-newest` | the newest sqlglot the range allows, which the lockfile never covers |

## The goldens are the specification

`samples/queries.sql` holds hand-formatted SQL, and `tests/fixtures/expected/`
holds what the formatter must produce for each one — **byte for byte**. They are
not regression snapshots to be regenerated when they disagree with the code;
they are the definition of the house style, and the code is what has to change.

If a change makes a golden fail, the question is which one is wrong. Usually the
code. Occasionally the golden, and then it changes deliberately, in its own
commit, with the reason.

A new construct needs a new golden. `scripts/build_fixtures.py` splits
`samples/queries.sql` into the fixture files after you add a sample.

## Two invariants every change must hold

**The output must mean what the input meant.** Every statement is re-parsed
after formatting and compared as a syntax tree; a difference means the statement
is passed through unchanged instead. If you find a decline whose reason is
`formatting would change semantics`, that is a **bug in the renderer**, not a
construct nobody implemented — `tests/test_no_silent_declines.py` exists to
catch exactly that, and it is worth reading before adding a layout handler.

**Formatting must be idempotent.** `format(format(x)) == format(x)`, always. A
surprising number of layout bugs are only visible on the second pass, so tests
for anything that moves content between lines should check a fixed point rather
than a single run.

## Declining is a feature

A construct the layout does not model is passed through byte-identical with a
warning, and the run still exits `0`. That is the contract, not a shortcoming —
it is what makes the tool safe to run across a repository you did not write.

When you add a decline, name it. `raise Unsupported("CTE MATERIALIZED")` tells
`--report` what to count and tells a user what to go and fix; a bare re-raise
tells them nothing.

## The docs are generated

`docs/v1/` is built and committed, because GitHub Pages runs no build step.
Prose lives in `docs/guide/*.md`; the settings examples and the CLI flag table
are produced by running the formatter and reading the argument parser, so
neither can drift from the tool.

After changing a guide page, a setting, or anything that changes output:

```sh
uv run python tools/build_docs.py
```

`tests/test_docs_site.py` fails if the committed site does not match what the
generator produces, so a forgotten rebuild cannot ship.

## Style

Match the surrounding code. Comments explain what the code is and why it has to
be that way — not what it used to be, or how a bug was found. That belongs in
the commit message.
