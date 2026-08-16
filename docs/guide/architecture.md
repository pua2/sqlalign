# Architecture

Read this page to understand why sqlalign's output looks the way it does, where to
add support for a construct it declines, and — precisely — how far its semantic
guarantee reaches and where it stops.

Two ideas explain almost everything else on this page:

1. **Layout and alignment are separate passes.** Handlers decide what goes on
   which line without knowing any final column; a second, deterministic pass
   resolves the columns and pads. That is why alignment composes with line
   wrapping instead of fighting it.
2. **Declining is a first-class result.** Anything the engine cannot reproduce
   exactly — an unmodelled construct, a render that would change meaning, an
   outright bug — degrades to a byte-identical passthrough with a warning. The
   file is never mangled.

## The pipeline

A file goes through this. The masking and the splitting happen once; everything
between them happens once per statement:

```
source text
  │
  ├─ mask Jinja/dbt expressions       templating.py   (same-width placeholders)
  ├─ split into statements            splitter.py     (lossless; parts rejoin byte-for-byte)
  │
  └─ for each statement:
       ├─ parse                       sqlglot.parse   (postgres | redshift | tsql)
       ├─ recover comments            layout/comments.py
       ├─ lay out to a Line/Seg IR    layout/*.py     (line breaks + indents fixed here)
       ├─ move separator commas       commas.py       (IR transform)
       ├─ drop disabled align targets align.py
       ├─ resolve columns and render  align.py        (fixpoint, then pad)
       ├─ apply keyword case          keywordcase.py  (AST-driven, only when `lower`)
       └─ re-parse and AST-compare    formatter.ast_equal
                                        │
                                        ├─ equal    → keep the formatted text
                                        └─ differs  → keep the ORIGINAL bytes + warn
  │
  ├─ rejoin statements                formatter._join_statements
  ├─ restore the template expressions  templating.py
  └─ restore line endings, write       cli.py
```

The safety check compares the **masked** input against the **masked** output, not
the raw text — raw templated SQL does not parse, so there would be nothing to
compare. Because the mask is a bijection over the same character positions,
equality of the masked forms is equality of the originals.

The modules:

| Module | Responsibility |
|---|---|
| `cli.py` | argparse, file/directory expansion and excludes, line endings, writing, exit codes |
| `configfile.py` | `.sqlalign.toml` / `[tool.sqlalign]` discovery, precedence, strict unknown-key checking |
| `style.py` | the `Style` dataclass (every knob), presets, `ALIGN_TARGETS`, `SUPPORTED_DIALECTS` |
| `config.py` | `Width` — the `width` / `grace` / `floor` triple and the effective break limit |
| `splitter.py` | cuts a file into statements; string-, comment-, dollar-quote- and `GO`-aware |
| `formatter.py` | orchestration, the AST safety check, the decline handling, inter-statement blank lines |
| `templating.py` | masks `{{ }}` / `{% %}` / `{# #}` with same-width placeholders |
| `plpgsql.py` | dollar-quoted body scanning, body statement splitting, plpgsql skeleton layout |
| `layout/` | one module per node family (select, fromjoin, conditions, case, window, cte, subquery, setop, dml, ddl, expr, grouporder) plus the comment engine |
| `ir.py` | `Seg`, `Line`, and the set of right-aligned kinds |
| `commas.py` | comma position, applied to the IR |
| `align.py` | align-target filtering, the fixpoint column resolver, and `render` |
| `casing.py` | `render_expr` and the ambient render style (`neq_style`, `decimal_style`) |
| `keywordcase.py` | the `lower` keyword pass, driven by the parse tree rather than a word list |

## Layout and alignment are two passes

The IR is deliberately small. A `Line` has an indent and a list of `Seg`s; a `Seg`
is text plus an optional `(scope, kind)` tag:

```python
@dataclass
class Seg:
    text: str
    scope: str | None = None
    kind: str | None = None    # item|op|alias|on|as|type|constraint|then
```

A layout handler emits lines and tags. It never counts spaces and never asks where
a column will land. `align.py` owns all column math.

That split is what makes the columnar style tractable. Consider what a handler
would otherwise have to know:

```sql
FROM customers               cust
INNER JOIN orders            ord        ON ord.customer_id     = cust.customer_id
LEFT JOIN order_line_items   line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr       ON addr.order_id       = ord.order_id
                                       AND addr.address_type   = 'shipping'
```

The alias column is set by the longest table reference (`LEFT JOIN
shipping_addresses`, row 4). The `ON` column is set by the longest *alias*
(`line_items`, row 3) — a different row. The operator column is set by row 3
again, but only *after* every `ON` has been pushed right. Three columns, three
different driving rows, and each depends on the resolution of the one before it.
No handler emitting row 1 could know any of that.

### Alignment composes with wrapping because it runs second

Line breaking happens in the layout pass, against the width limit, before any
padding exists. Alignment then pads whatever lines came out. So a wrapped item
still participates in its column:

```sql
SELECT customer_id
     , order_id
     , order_date
     , total
     , ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC) AS rn
     , SUM(total) OVER (PARTITION BY customer_id ORDER BY order_date
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)    AS running_total
     , LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date)   AS prev_order_date
FROM orders;
```

The second window function wrapped inside its `OVER (...)`. Its `AS` still lands
in the same column as the two unwrapped siblings, because the `AS` column is
resolved over the *rendered lines*, and the last line of a wrapped item is just
another line.

The ordering is one-way on purpose: breaking runs before
alignment, and alignment is never sacrificed to fit the width limit. Padding that
pushes a line past the limit is accepted rather than fed back into the breaker.
Feeding it back is what makes an aligning formatter oscillate — pad, overflow,
break, un-pad, fit, pad again.

The width limit itself is a soft target, computed per construct:

```python
def limit(self, anchor: int) -> int:
    return max(self.width, anchor + self.floor) + self.grace
```

with `width=100`, `grace=5`, `floor=60` by default. A construct anchored deep in a
subquery still gets 60 columns of working room, and nothing breaks over five
columns of overshoot.

### The second pass is genuinely separable

Because padding is the only thing the alignment pass adds, switching it off leaves
the line structure untouched. Same input, `--no-align`:

```sql
SELECT cust.customer_id
     , cust.email
     , ord.order_id
     , ord.total
     , line_items.product_id
     , line_items.quantity
     , addr.city
FROM customers cust
INNER JOIN orders ord ON ord.customer_id = cust.customer_id
LEFT JOIN order_line_items line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr ON addr.order_id = ord.order_id
  AND addr.address_type = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment = 'enterprise';
```

Every line break is where it was. `render(lines, align=False)` skips column
resolution entirely and joins segments with one space; the continuation `AND` falls
back to its layout indent because there is no longer an `ON` column to justify
against. `--align-targets` is the same mechanism at finer grain: a disabled target
has its `scope` and `kind` cleared, so the resolver simply never sees it and that
one column collapses to single spaces. There is no separate unaligned code path
that could drift from the aligned one.

The same reasoning drives `--comma-position`. Moving a separator comma is an IR
transform — each comma is its own tagged segment — not a rewrite over printed
text. A text pass cannot reliably tell a separator comma from the comma in
`NUMERIC(10, 2)`, and it cannot know which physical line ends a multi-line item.
Doing it on the IR also means alignment resolves against the final segment text
rather than against text that is about to move.

## The fixpoint resolver

Column resolution is not a single left-to-right sweep. It cannot be: a tagged
segment's position depends on the resolved targets of the tagged segments to its
left on the same line, and those targets may be driven by a different row.

`_resolve_targets` iterates instead. Each sweep replays the emit pass using the
previous sweep's targets — when the running column reaches a tagged segment it is
placed at `max(natural_position, target_so_far)` and the cursor advances past the
*placed* position, not the natural one. Targets only ever increase, and they are
bounded by total line length, so the iteration terminates; it stops as soon as a
full sweep changes nothing.

Two properties are worth knowing if you are reading `align.py`:

- **The first sweep is the naive resolver.** It starts from all-zero targets, so
  every `max(natural, 0)` is just `natural`. Any scope with at most one tagged
  column per line fixes in one sweep, at exactly the columns a single-pass
  resolver would produce.
- **Later sweeps can only raise a column.** They cannot lower one or move it
  sideways, so the iteration can correct an under-computed column but cannot
  invent a different layout.

Right-aligned kinds (`op`, `on`) are resolved by their *end* column; everything
else by its start. That is what puts `=` and `>=` in one column with their
operands ragged on the left:

```sql
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment    = 'enterprise';
```

## The safety model

After a statement is rendered, sqlalign parses the output again and compares its
AST to the input's. If they differ, the **original bytes** are kept and a warning
goes to stderr. This is per statement: one statement declining does not stop the
rest of the file from formatting.

The comparison is not raw `==` on trees. `_normalize` first walks both sides and
neutralises differences that cannot carry meaning:

| Normalisation | Why |
|---|---|
| `comments = None` on every node | a comment cannot change meaning |
| `LANGUAGE plpgsql` vs `LANGUAGE 'plpgsql'` | optional legacy quoting; parses to `Var` vs `Literal` |
| casefold `WindowSpec.kind` / `start_side` / `end_side`, `TruncateTable.identity` / `option` | plain strings holding source-cased *keywords* |
| casefold `DistStyleProperty`'s `Var` | a closed keyword set (`KEY`/`EVEN`/`ALL`/`AUTO`), not user data |
| casefold `Command` text *outside quotes* | half-parsed raw source; quoted identifiers and string literals keep their case |

The exclusions are as deliberate as the inclusions. A bare `exp.Var` is **not**
casefolded globally, because `SET search_path = "MySchema"` parses to
`Var(this="MySchema")` with the quotes already stripped — folding all `Var.this`
would make a real, meaning-changing case edit invisible.

Two constructs get a structural comparison instead of a whole-text one:

- **Dollar-quoted bodies.** sqlglot sees `$$ … $$` as one opaque string, so a raw
  AST comparison would reject *any* body reformatting. Header and tail are
  compared as an AST (an empty-body `CREATE` that parses); the body is split into
  clauses; each embedded SQL statement is compared as an AST; skeleton statements
  (`DECLARE`, `BEGIN`, `IF … THEN`) are compared whitespace- and
  case-insensitively.
- **T-SQL `GO` batches.** `GO` is a client directive, not SQL, and sqlglot
  swallows the statement after it into a string literal — so a whole-file parse
  compares nonsense. The file is split at `GO` lines, each batch compared
  separately, and the separators themselves compared so a dropped or added `GO`
  is still caught.

### What the guarantee does not cover

Three limits. All three are real, and none of them can be closed by a better AST
comparison.

**1. It is a within-dialect guarantee.** `SUPPORTED_DIALECTS` is a whitelist of
`postgres`, `redshift` and `tsql`, and it is not a list of dialects sqlglot can
parse — sqlglot parses many more. It is the list whose *emitted keywords* have
been audited. The failure mode this guards against actually shipped once: sqlglot
parses T-SQL `SELECT TOP 10` into the same `Limit` node Postgres uses; a
dialect-agnostic handler emitted its hard-coded `LIMIT`; sqlglot then parsed that
`LIMIT` back as T-SQL too, so `ast_equal` compared *equal* and the statement went
out as `SELECT id … LIMIT 10`, which SQL Server rejects outright. Valid SQL in,
broken SQL out, no warning. The AST net cannot see this, because the round trip
never leaves sqlglot's more permissive grammar. `format_sql` therefore raises a
`ValueError` for any dialect outside the whitelist rather than producing output,
and extending the list means auditing every keyword the handlers emit — two of
them diverge for T-SQL — not just registering a parser.

**2. Comments are excluded from `ast_equal`.** Stripping them is correct for
semantics and unavoidable in practice, but it means a dropped, restyled
(`--` → `/* */`), or relocated comment is **silent** as far as the safety net is
concerned. The byte-exact golden fixtures are the only guard on comment handling.
That is precisely why `layout/comments.py` recovers each comment's original style
and text from the raw source and re-attaches it by authorial position, and why it
raises `Unsupported` for any comment position it does not model instead of
emitting a best-effort guess. "Reproduce faithfully or decline" is the contract;
"try our best" would be unverifiable.

**3. Some distinctions are destroyed before the comparison exists.** When
sqlglot's parser collapses two spellings into one node, both sides of the
comparison collapse identically and the difference is invisible. Where the two
spellings are true synonyms this is harmless, and sqlalign simply picks one:
`!=` / `<>` and `NUMERIC` / `DECIMAL` are the only two places it chooses for you,
and both are configurable. Where they are *not* synonyms, the AST is useless and
the only defence is the raw source: T-SQL `REAL` (FLOAT(24)) and `FLOAT`
(FLOAT(53)) collapse to one node, as do `NTEXT` (Unicode) and `TEXT`, so a
statement containing `REAL` or `NTEXT` under `--dialect tsql` is declined on sight
by a regex over the input text.

Where the collapse *is* visible, the net does its job. Redshift `TEXT` renders as
`VARCHAR(MAX)` through sqlglot, which widens the column from 256 to 65535 — but
those parse to different nodes, so:

```console
$ sqlalign --stdout --dialect redshift table.sql
sqlalign: table.sql: sqlglot cannot round-trip this statement, passed through unformatted: create table t (a text not null, b int);
create table t (a text not null, b int);
```

## The decline contract

Everything above funnels into one behaviour: **when in doubt, emit the input
bytes and say so.**

`formatter._format_all` wraps layout, render and the safety check per statement,
and every failure class degrades to the same passthrough with a differently worded
warning, so an expected decline is distinguishable from a bug in stderr:

| Condition | Warning |
|---|---|
| sqlglot cannot parse the statement | `passthrough (parse error line N): …` |
| a layout handler raised `Unsupported` | `unsupported construct, passed through: …` |
| the output would not AST-compare equal | `formatting would change semantics, passed through unformatted: …` — **a bug report**, not a decline: the renderer emitted something that means a different thing |
| …and sqlglot cannot round-trip the input either | `sqlglot cannot round-trip this statement, passed through unformatted: …` — the fault is upstream, so no formatter built on sqlglot could satisfy the check |
| any other exception | `internal formatter error, passed through (please report): …` |

One decline is file-wide rather than per statement: a template expression too
short to hold a same-width placeholder (`{{x}}`) fails before splitting, so the
whole file passes through with `templating not maskable, passed through: …`. It
has to be — masking runs over the file, not over one statement.

Otherwise declining is per statement, not per file:

```console
$ sqlalign --stdout mixed.sql
sqlalign: mixed.sql: unsupported construct, passed through: -- a decline next to a formatted stateme
-- a decline next to a formatted statement
select distinct on (a) a, b from t;
SELECT x
     , y
FROM z
WHERE a  = 1
  AND bb = 2;
```

Exit code `0`. A construct sqlalign does not model is not an error — the file is
still correct SQL, byte-for-byte where it was not understood and formatted
everywhere else.

### Why this is the product, not a limitation

A formatter that reformats what it half-understands is worse than one that
declines, because you cannot tell the two apart by reading the diff. Every
declined statement is *visibly* unchanged and *audibly* reported, so the coverage
gap is legible; a wrong render is not.

The contract is enforced structurally rather than by discipline. `layout_statement`
raises `Unsupported` for any node type it does not dispatch, so a new construct
declines by default until someone writes its handler. Within a handler,
`guard_args` declines a node carrying any argument the handler does not read:

```python
for name, value in node.args.items():
    if value not in (None, [], False) and name not in allowed:
        raise Unsupported(f"{label or type(node).__name__} arg: {name}")
```

Without that, an `INSERT … ON CONFLICT` or an unread `CREATE TABLE` option would
be silently *dropped* from the output — a change the AST comparison would catch,
but only after the handler had already produced wrong text. Declining at the arg
level makes it a passthrough instead of a near miss.

The same rule governs the edges of the pipeline. The splitter is lossless — its
parts concatenate back to the input byte for byte — specifically so a declined
statement can be emitted from its part verbatim. Template masking uses same-width
placeholders and declines an expression too short to hold one (`{{x}}`), rather
than approximating a width and shifting every column in the file. A file with lone
`CR` line endings, which sqlalign does not model, passes through untouched with a
warning instead of being silently rewritten.

Two escape hatches exist for SQL that parses fine but that you want left alone:
`-- sqlalign: skip` on the line above a statement passes exactly that statement
through, with no parse, no layout and deliberately no warning; `--no-format-bodies`
does the same for every dollar-quoted procedure body.

```console
$ sqlalign --stdout skip.sql
-- sqlalign: skip
select   a,b   from t where x=1;
SELECT a
     , b
FROM t
WHERE x = 1;
```

## Testing

The design spec describes the style; the fixtures are what the suite actually
enforces. Treat them as the executable specification — grow them, never weaken
them.

**29 byte-exact golden pairs** live in `tests/fixtures/input/NN.sql` and
`tests/fixtures/expected/NN.sql`, generated from the 29 hand-formatted samples in
`samples/queries.sql` (24 postgres, one redshift, four T-SQL). Every pair is
asserted three ways:

| Assertion | What it catches |
|---|---|
| `format(input) == expected`, byte for byte | any layout regression at all |
| `format(expected) == expected` | a rule that keeps nudging a line every pass — invisible to a one-pass test |
| input and output parse to equal ASTs | the runtime safety net's own check, run over the corpus |

A **sqlfluff gate** lints every standard-SQL Postgres expected fixture against the
repo's `.sqlfluff`, with three rules excluded and each exclusion documented in
that file (the house style preserves `GROUP BY` reference form, does not force
aliases, and keeps `::` vs `CAST(...)` as written). The Redshift, T-SQL and
plpgsql fixtures sit outside the gate — dialect and linter-model limits, each
noted in `tests/test_sqlfluff_gate.py`.

Goldens pin the **default** style. They cannot pin the knobs: enumerating knob
combinations byte-for-byte would need one fixture per combination, which is how
"just add more options" rots a formatter. `tests/test_knob_combinations.py`
asserts **invariants across combinations** instead, drawn from a fixed seed so a
failure reproduces. For every sample and every sampled combination:

1. semantics never change (`ast_equal(input, output)`),
2. the output is idempotent,
3. the output re-parses,
4. no line has trailing whitespace,
5. the token stream is identical to the house rendering — layout knobs move
   tokens, they never add, drop, or reorder them.

Two of those tests guard the test, which is worth copying: one asserts the sampled
combinations actually produce *different* output (a property suite that exercises
one behaviour ten times proves nothing), and one asserts invariant 5 **fails**
when tokens are dropped or reordered (an invariant that cannot fail is
indistinguishable from one that passes). A third pins that the defaults still
reproduce every golden, so adding a knob cannot quietly change what sqlalign does
when nobody configures it.

Below that sit unit tests for the alignment formula, the fixpoint composition
cases, the splitter, the dollar-quote scanner, comments, config precedence, file
selection, line endings, and each individual knob. Run the lot with:

```sh
PYTHONPATH=src pytest -q
```

## Adding a construct

1. Write the golden first. Add the hand-formatted statement to
   `samples/queries.sql` under a new `-- #N: description` header, regenerate with
   `python scripts/build_fixtures.py samples/queries.sql tests/fixtures/expected`
   (it writes zero-padded `NN.sql`), and add the messy
   `tests/fixtures/input/NN.sql`.
2. Run `PYTHONPATH=src pytest -q`. A new golden that fails is either a real gap in
   a layout handler or an unmodelled construct — in the second case confirm you
   get the *warning and a passthrough*, not a wrong render.
3. Emit tags, never spaces. A handler adds `Seg(text, scope, kind)`; if you find
   yourself computing a column width in `layout/`, the logic belongs in
   `align.py`.
4. Read every layout literal from `Style`. Handlers do not hard-code widths,
   indents, or spellings.
5. Widen `guard_args` only for arguments you actually render.
6. If the new output should lint clean, check the sqlfluff gate still passes, or
   document a new exclusion in `.sqlfluff` with a comment explaining it.

## See also

- [Dialects](dialects.md) — what each of the three dialects covers, and the
  keyword audit behind the whitelist.
- [The house style](style.md) — the output this engine produces, construct by
  construct.
- [FAQ](faq.md) — the user-facing version of the decline contract: why a
  statement was passed through, and what to do about it.
- **Style reference** — `samples/queries.sql`: the hand-formatted goldens that
  define the house style, and the only place it is defined byte-for-byte.
