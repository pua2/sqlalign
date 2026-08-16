# The house style

Read this page to see exactly what sqlalign does to your SQL, construct by construct,
and to find the handful of decisions you can change.

Every SQL block below is real output from `sqlalign --stdout`, paired with the
messy input that produced it. A few later blocks reuse an input shown earlier.
Nothing here is hand-written "expected" SQL.

If you only want the summary: sqlalign stacks lists one item per line with the
separator leading, and pads repeated elements into vertical columns. It does not
change what your SQL means, and it does not lint.

## Two rules generate most of the layout

**One element per line, separator first.** In any stacked list — select items,
`GROUP BY` columns, `SET` assignments, `CREATE TABLE` columns — each item gets its
own line, opened by the comma rather than closed by it. In a clause, the first
item rides the clause keyword's line.

**Repeated elements pad into a column.** Table aliases, `AS` aliases, `ON`
keywords, comparison operators, `THEN` values, column types: each kind gets one
column per scope, wide enough for the longest entry.

Two details make the columns look the way they do:

- Everything except operators is **left-aligned** and starts one space past the
  longest content to its left.
- **Operators right-align.** Their *end* is what lines up, so `=`, `>=`, `IN`,
  `IS` and `BETWEEN` all finish in the same column and their right-hand operands
  start together.

A **scope** is the region a column spans: one select list, one whole `FROM`
block, one `WHERE` clause, one `CASE`. Brackets end a scope, so a subquery or CTE
aligns internally and never with its parent.

## The select list

```sql
select id, first_name, last_name, email, created_at from users where status = 'active' and created_at >= '2026-01-01' order by created_at desc limit 100;
```

```sql
SELECT id
     , first_name
     , last_name
     , email
     , created_at
FROM users
WHERE status      = 'active'
  AND created_at >= '2026-01-01'
ORDER BY created_at DESC
LIMIT 100;
```

The first item stays on the `SELECT` line. Every later item starts in that same
column, with its comma two columns to the left. `*` is never expanded into
columns.

When items carry aliases, the `AS` keywords form a column across the select list.
Select-list aliases always render with `AS`, so `SELECT a x` comes back as
`SELECT a AS x` — but sqlalign never invents an alias where you had none, and
never changes the name you chose:

```sql
select customer_id, date_trunc('month', order_date) as order_month, count(*) as order_count, sum(total) as revenue, avg(total) as avg_order_value from orders where status = 'complete' group by customer_id, date_trunc('month', order_date) having count(*) > 3 and sum(total) > 1000 order by revenue desc;
```

```sql
SELECT customer_id
     , DATE_TRUNC('month', order_date) AS order_month
     , COUNT(*)                        AS order_count
     , SUM(total)                      AS revenue
     , AVG(total)                      AS avg_order_value
FROM orders
WHERE status = 'complete'
GROUP BY customer_id
       , DATE_TRUNC('month', order_date)
HAVING COUNT(*)   > 3
   AND SUM(total) > 1000
ORDER BY revenue DESC;
```

### GROUP BY, ORDER BY, LIMIT

`GROUP BY` and `ORDER BY` use the same stacking rule as the select list, with two
exceptions. A **single item stays inline** — `ORDER BY revenue DESC` above. So do
**positional references**, however many: `GROUP BY 1, 2` stays on one line (see the
CTE example further down). Two or more named columns stack. `LIMIT` gets its own
line.

### Set operations

A set operator sits alone on its line with a blank line on each side, and each
branch is formatted independently:

```sql
select id, email, 'customer' as source from customers where created_at >= '2026-01-01' union all select id, email, 'lead' as source from leads where converted = false union all select id, email, 'partner' as source from partner_contacts;
```

```sql
SELECT id
     , email
     , 'customer' AS source
FROM customers
WHERE created_at >= '2026-01-01'

UNION ALL

SELECT id
     , email
     , 'lead' AS source
FROM leads
WHERE converted = FALSE

UNION ALL

SELECT id
     , email
     , 'partner' AS source
FROM partner_contacts;
```

## The FROM block

Table aliases align into one column that spans the whole `FROM` block — the base
table and every join together, not each join on its own. Table aliases render
implicitly, so `FROM orders AS o` comes back as `FROM orders o`.

```sql
select cust.customer_id, cust.email, ord.order_id, ord.total, line_items.product_id, line_items.quantity, addr.city from customers cust inner join orders ord on ord.customer_id = cust.customer_id left join order_line_items line_items on line_items.order_id = ord.order_id left join shipping_addresses addr on addr.order_id = ord.order_id and addr.address_type = 'shipping' where ord.order_date >= '2026-07-01' and cust.segment = 'enterprise';
```

```sql
SELECT cust.customer_id
     , cust.email
     , ord.order_id
     , ord.total
     , line_items.product_id
     , line_items.quantity
     , addr.city
FROM customers               cust
INNER JOIN orders            ord        ON ord.customer_id     = cust.customer_id
LEFT JOIN order_line_items   line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr       ON addr.order_id       = ord.order_id
                                       AND addr.address_type   = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment    = 'enterprise';
```

`shipping_addresses` is the longest table name, so the alias column opens one
space past it and `cust`, `ord`, `line_items` and `addr` all start there.

## The ON/AND condition column

This is the part of the style you are least likely to have seen before. Look at
the same block again:

```sql
FROM customers               cust
INNER JOIN orders            ord        ON ord.customer_id     = cust.customer_id
LEFT JOIN order_line_items   line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr       ON addr.order_id       = ord.order_id
                                       AND addr.address_type   = 'shipping'
```

Three columns run the full height of the block: the aliases, the `ON` keyword, and
the `=` operators. The join conditions therefore read as a two-column table of left
operand and right operand — across every join at once, not per join. (Table names
are not a column; they start wherever their join keyword ends.)

An extra condition drops to its own line and its `AND` right-aligns to end where
`ON` ends, so `ON` and `AND` read as one stack of connectives.

Aligning aliases is not unusual — sqlfluff aligns alias expressions too. Carrying
one `ON`/`AND` column across an entire `FROM` block is the unusual part: the other
formatters surveyed in the README break the line instead of padding it. If you
want it gone, `--align-targets` drops `join_conditions` and keeps the rest.

## WHERE and HAVING

The first condition rides the keyword's line. Later conditions get their own
lines, and their `AND`/`OR` right-aligns to **end where the keyword ends** —
column 5 for `WHERE`, column 6 for `HAVING`. That is why `AND` is indented two
under `WHERE` and three under `HAVING`.

Within the clause, comparison operators right-align:

```sql
SELECT id, name, category, price FROM products WHERE (category IN ('electronics', 'appliances', 'accessories') OR name LIKE '%refurb%') AND price BETWEEN 25 AND 500 AND discontinued = false AND supplier_id IS NOT NULL;
```

```sql
SELECT id
     , name
     , category
     , price
FROM products
WHERE (category  IN ('electronics', 'appliances', 'accessories')
       OR name LIKE '%refurb%')
  AND price  BETWEEN 25 AND 500
  AND discontinued = FALSE
  AND supplier_id IS NOT NULL;
```

Two things are worth reading closely there.

**Operators end together, they do not start together.** `BETWEEN`, `=` and `IS`
are 7, 1 and 2 characters wide, and all three finish in the same column, so the
values to their right line up.

**A parenthesized group is its own sub-scope.** `IN` and `LIKE` align with each
other inside the parens; `BETWEEN`, `=` and `IS` align with each other outside it.
The two groups do not share a column. Inside the group, `OR` aligns with the
group's first condition rather than with the outer `AND`.

## CASE

sqlalign has two `CASE` forms and picks between them automatically.

**Short form** puts each `WHEN cond THEN val` on one line, with the `WHEN`s
aligned one space past `CASE` and the `THEN`s padded into a column. `END` sits
under `CASE`.

**Long form** puts `THEN` on its own line, indented two past its `WHEN`. You get
it when a `WHEN` condition is a compound boolean (`AND`/`OR`), or when a
`WHEN … THEN …` row would not fit the width limit.

Both forms in one query — the second `CASE` goes long because its second `WHEN`
is compound, even though every row would have fit:

```sql
select order_id, total, case when total >= 1000 then 'large' when total >= 100 then 'medium' when total > 0 then 'small' else 'invalid' end as order_size, case when status = 'complete' then 1 when status = 'pending' and not is_archived then 2 else 99 end as status_rank from orders;
```

```sql
SELECT order_id
     , total
     , CASE WHEN total >= 1000 THEN 'large'
            WHEN total >= 100  THEN 'medium'
            WHEN total > 0     THEN 'small'
            ELSE 'invalid'
       END AS order_size
     , CASE WHEN status = 'complete'
              THEN 1
            WHEN status = 'pending'
             AND NOT is_archived
              THEN 2
            ELSE 99
       END AS status_rank
FROM orders;
```

`CASE` is never left alone on a line: the first `WHEN` always stays with it.

Here is the width trigger on its own — no compound condition, just a result string
too long for the line:

```sql
select order_id, case when total >= 1000 then 'a really quite long result string that pushes this row over the configured width limit' when total > 0 then 'small' else 'none' end as bucket from orders;
```

```sql
SELECT order_id
     , CASE WHEN total >= 1000
              THEN 'a really quite long result string that pushes this row over the configured width limit'
            WHEN total  > 0
              THEN 'small'
            ELSE 'none'
       END AS bucket
FROM orders;
```

## Window functions

`OVER` takes a space before its paren — it is a keyword, not a function name. A
window that fits stays on one line. One that does not breaks at window sub-clause
boundaries, with continuation lines aligned under `PARTITION`. The `AS` alias
still joins the select list's alias column, measured against the last line:

```sql
select customer_id, order_id, order_date, total, row_number() over (partition by customer_id order by order_date desc) as rn, sum(total) over (partition by customer_id order by order_date rows between unbounded preceding and current row) as running_total, lag(order_date) over (partition by customer_id order by order_date) as prev_order_date from orders;
```

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

The frame clause is the first thing to move down when a window is too wide.

## CTEs

Each CTE opens with `name AS (`, indents its body two, and closes with `)` at the
statement's anchor column. A blank line separates CTEs from each other and from
the final `SELECT`, and the separator comma opens the next CTE's line:

```sql
with monthly_revenue as (select customer_id, date_trunc('month', order_date) as month, sum(total) as revenue from orders group by 1, 2), top_customers as (select customer_id from monthly_revenue group by customer_id having sum(revenue) > 10000) select m.customer_id, m.month, m.revenue from monthly_revenue m join top_customers t on t.customer_id = m.customer_id order by m.customer_id, m.month;
```

```sql
WITH monthly_revenue AS (
  SELECT customer_id
       , DATE_TRUNC('month', order_date) AS month
       , SUM(total)                      AS revenue
  FROM orders
  GROUP BY 1, 2
)

, top_customers AS (
  SELECT customer_id
  FROM monthly_revenue
  GROUP BY customer_id
  HAVING SUM(revenue) > 10000
)

SELECT m.customer_id
     , m.month
     , m.revenue
FROM monthly_revenue m
JOIN top_customers   t ON t.customer_id = m.customer_id
ORDER BY m.customer_id
       , m.month;
```

The `AS` column inside `monthly_revenue` is computed from that CTE's items alone.
`top_customers` and the final `SELECT` each resolve their own columns; the
brackets keep them apart.

`WITH RECURSIVE` gets the same layout, with the set operator inside the CTE body
following the usual blank-line rule:

```sql
with recursive tree as (select id, parent_id, 1 as depth from nodes where parent_id is null union all select n.id, n.parent_id, t.depth + 1 from nodes n join tree t on t.id = n.parent_id) select id, depth from tree order by depth;
```

```sql
WITH RECURSIVE tree AS (
  SELECT id
       , parent_id
       , 1 AS depth
  FROM nodes
  WHERE parent_id IS NULL

  UNION ALL

  SELECT n.id
       , n.parent_id
       , t.depth + 1
  FROM nodes n
  JOIN tree  t ON t.id = n.parent_id
)

SELECT id
     , depth
FROM tree
ORDER BY depth;
```

### Subqueries

A derived table opens inline after `(`, aligns its body to its own `SELECT`, and
drops the closing `)` to a line of its own, with the alias and `ON` following it
there. Scalar, `IN (…)` and `EXISTS (…)` subqueries close inline instead:

```sql
select u.id, u.email, recent.last_order_date, (select count(*) from support_tickets st where st.user_id = u.id) as ticket_count from users u join (select customer_id, max(order_date) as last_order_date from orders group by customer_id) recent on recent.customer_id = u.id where u.id in (select user_id from subscriptions where plan = 'premium');
```

```sql
SELECT u.id
     , u.email
     , recent.last_order_date
     , (SELECT COUNT(*)
        FROM support_tickets st
        WHERE st.user_id = u.id) AS ticket_count
FROM users u
JOIN (SELECT customer_id
           , MAX(order_date) AS last_order_date
      FROM orders
      GROUP BY customer_id
     ) recent ON recent.customer_id = u.id
WHERE u.id IN (SELECT user_id
               FROM subscriptions
               WHERE plan = 'premium');
```

A derived table is deliberately excluded from the `FROM` block's alias and `ON`
columns — its alias sits on the closing-paren line, anchored to the subquery.

## INSERT, UPDATE, DELETE

`INSERT INTO table` takes its own line. The column list uses the same leading
separator as a select list, opened by `(` and two spaces, and closed by `)`
touching the last column. The `SELECT` then starts at column 1. (A top-level
`INSERT … VALUES` is not modelled yet and passes through byte-identical; `VALUES`
inside a `MERGE` is formatted, as below.)

`UPDATE … SET` stacks assignments with the `=` aligned. `DELETE FROM` is followed
by a normal `WHERE` clause.

```sql
insert into daily_revenue (report_date, channel, revenue, order_count) select order_date, channel, sum(total), count(*) from orders where order_date = current_date - 1 group by order_date, channel;
update products set price = price * 1.05, updated_at = current_timestamp where category = 'electronics' and discontinued = false;
delete from daily_revenue where report_date < '2026-01-01' and channel = 'legacy';
```

```sql
INSERT INTO daily_revenue
(  report_date
 , channel
 , revenue
 , order_count)
SELECT order_date
     , channel
     , SUM(total)
     , COUNT(*)
FROM orders
WHERE order_date = CURRENT_DATE - 1
GROUP BY order_date
       , channel;

UPDATE products
SET price      = price * 1.05
  , updated_at = CURRENT_TIMESTAMP
WHERE category     = 'electronics'
  AND discontinued = FALSE;

DELETE FROM daily_revenue
WHERE report_date < '2026-01-01'
  AND channel     = 'legacy';
```

The blank lines between those three statements are not from the input, which had
none. sqlalign puts exactly one blank line between two statements when **both are
multi-line**, and leaves adjacency alone otherwise. `--blank-lines-between-statements N`
forces a fixed count instead.

## MERGE

Every `MERGE` clause starts its own line at column 1. The `ON` block uses
`WHERE`-clause geometry — `ON` and `AND` right-aligned to end at column 5, with
the operators padded — and `SET` behaves exactly like an `UPDATE`:

```sql
merge into daily_revenue tgt using staging_daily_revenue src on tgt.report_date = src.report_date and tgt.channel = src.channel when matched then update set revenue = src.revenue, order_count = src.order_count, updated_at = current_timestamp when not matched then insert (report_date, channel, revenue, order_count) values (src.report_date, src.channel, src.revenue, src.order_count);
```

```sql
MERGE INTO daily_revenue tgt
USING staging_daily_revenue src
   ON tgt.report_date = src.report_date
  AND tgt.channel     = src.channel
WHEN MATCHED
THEN UPDATE
SET revenue     = src.revenue
  , order_count = src.order_count
  , updated_at  = CURRENT_TIMESTAMP
WHEN NOT MATCHED
THEN INSERT
(  report_date
 , channel
 , revenue
 , order_count)
VALUES
(  src.report_date
 , src.channel
 , src.revenue
 , src.order_count
);
```

## CREATE TABLE

One column per line. The first is indented 4, the leading commas sit at column 3,
and three columns align: name, type, then constraints. A table-level constraint
is the last list item.

```sql
create table daily_revenue (report_date date not null, channel varchar(50) not null, revenue numeric(12,2) default 0, order_count int default 0, created_at timestamp default current_timestamp, primary key (report_date, channel));
```

```sql
CREATE TABLE daily_revenue (
    report_date DATE           NOT NULL
  , channel     VARCHAR(50)    NOT NULL
  , revenue     NUMERIC(12, 2) DEFAULT 0
  , order_count INT            DEFAULT 0
  , created_at  TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
  , PRIMARY KEY (report_date, channel)
);
```

Note `NUMERIC(12,2)` became `NUMERIC(12, 2)`: inline lists always take a space
after the comma.

On Redshift, constraints align **per kind** rather than sharing one column, and
table attributes follow the closing paren, one clause per line. `ENCODE` values
are identifiers, so they come through exactly as you wrote them — `az64` stays
lowercase, `AZ64` stays uppercase:

```sql
create table fact_orders (order_id bigint not null, customer_id bigint not null encode az64, order_date date not null encode az64, total numeric(12, 2) encode az64, channel varchar(32) encode lzo) diststyle key distkey (customer_id) compound sortkey (order_date, customer_id);
```

```sql
CREATE TABLE fact_orders (
    order_id    BIGINT NOT NULL
  , customer_id BIGINT NOT NULL ENCODE az64
  , order_date  DATE   NOT NULL ENCODE az64
  , total       NUMERIC(12, 2)  ENCODE az64
  , channel     VARCHAR(32)     ENCODE lzo
)
DISTSTYLE KEY DISTKEY (customer_id)
COMPOUND SORTKEY (order_date, customer_id);
```

The `NOT NULL` column is measured over only the three lines that carry it; the
`ENCODE` column over only the four that carry it. That is why `total` and
`channel` have no `NOT NULL` gap to pay for.

## plpgsql `$$` bodies

sqlalign parses the body between `$$ … $$` and formats it with the same engine it
uses at the top level, rather than treating it as an opaque string. Header clauses
each get their own line, body statements sit at column 1 with blank lines between
them, and `$$;` closes at column 1.

```sql
create or replace function get_customer_ltv(p_customer_id int) returns numeric language plpgsql as $$ declare v_ltv numeric; begin select sum(total) into v_ltv from orders where customer_id = p_customer_id and status = 'complete'; if v_ltv is null then v_ltv := 0; end if; return v_ltv; end; $$;
```

```sql
CREATE OR REPLACE FUNCTION get_customer_ltv(p_customer_id INT)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$

DECLARE v_ltv NUMERIC;

BEGIN

SELECT SUM(total) INTO v_ltv
FROM orders
WHERE customer_id = p_customer_id
  AND status      = 'complete';

IF v_ltv IS NULL
  THEN v_ltv := 0;
END IF;

RETURN v_ltv;

END;
$$;
```

The `WHERE` clause inside the body is aligned by the same rules as one at the top
level. A larger body shows the full treatment — `DELETE`, an `INSERT` with its
column list, `GET DIAGNOSTICS`, and an `IF`/`THEN`/`ELSE` block:

```sql
create procedure refresh_daily_revenue(target_date date) language plpgsql as $$ declare row_count int; begin delete from daily_revenue where report_date = target_date; insert into daily_revenue (report_date, channel, revenue, order_count) select order_date, channel, sum(total), count(*) from orders where order_date = target_date group by order_date, channel; get diagnostics row_count = row_count; if row_count = 0 then raise warning 'no orders found for %', target_date; else raise notice 'loaded % rows', row_count; end if; end; $$;
```

```sql
CREATE PROCEDURE refresh_daily_revenue(target_date DATE)
LANGUAGE plpgsql
AS $$

DECLARE row_count INT;

BEGIN

DELETE FROM daily_revenue
WHERE report_date = target_date;

INSERT INTO daily_revenue
(  report_date
 , channel
 , revenue
 , order_count)
SELECT order_date
     , channel
     , SUM(total)
     , COUNT(*)
FROM orders
WHERE order_date = target_date
GROUP BY order_date
       , channel;

GET DIAGNOSTICS row_count = ROW_COUNT;

IF row_count = 0
  THEN RAISE WARNING 'no orders found for %', target_date;
  ELSE RAISE NOTICE 'loaded % rows', row_count;
END IF;

END;
$$;
```

One header normalization to know about: `LANGUAGE 'plpgsql'` loses its quotes and
comes back as `LANGUAGE plpgsql`. The quoted form is legacy syntax; the Postgres
and Redshift docs both use the unquoted one.

`--no-format-bodies` turns body formatting off. Note what that does: the whole
`CREATE FUNCTION`/`CREATE PROCEDURE` statement is left byte-identical, header
included, while other statements in the same file still format.

## What the style deliberately does not do

**It cannot change what your SQL means.** Every formatted statement is re-parsed
and AST-compared against the input. Anything that would differ semantically is
discarded and the original bytes are kept. Identifiers, string literals, cast form
(`::` versus `CAST`), `GROUP BY` reference form and the alias names you chose all
survive exactly as written. (The `AS` keyword itself is layout, not content, so it
is normalized: present in select lists, absent for table aliases.)

**It does not lint.** sqlalign will not unify your cast styles, insert missing
aliases, turn `JOIN` into `INNER JOIN`, or make `GROUP BY` references consistent.
That is sqlfluff's job, and the two are designed to run together.

**It declines what it cannot reproduce exactly.** A construct the engine does not
model passes through byte-identical, with a warning on stderr and exit code 0.
`JOIN … USING` is one such construct today:

```sql
select a.id from a join b using (tenant_id);
```

```sql
select a.id from a join b using (tenant_id);
```

```
sqlalign: query.sql: unsupported construct, passed through: select a.id from a join b using (tenant_
```

Nothing was reformatted and nothing was mangled. The rest of the file still
formats normally.

## What you can configure

Eleven settings change the style itself. Each is a CLI flag and a `.sqlalign.toml`
key (or a `[tool.sqlalign]` key in `pyproject.toml`), and each can also arrive via
a preset. `sqlalign --show-config file.sql` prints what is actually in effect.

| Flag | Config key | Default | Changes |
|---|---|---|---|
| `--no-align` | `align` | `true` | all column padding, on or off |
| `--align-targets a,b,…` | `align_targets` | all but `table_names` | which columns are padded |
| `--comma-position` | `comma_position` | `leading` | where the separator comma sits |
| `--boolean-operator-position` | `boolean_operator_position` | `leading` | where `AND`/`OR` sit |
| `--on-placement` | `on_placement` | `inline` | whether a join's `ON` rides the table line |
| `--keyword-case` | `keyword_case` | `upper` | case of keywords, functions and types |
| `--select-placement` | `select_placement` | `inline` | whether the first select item rides the `SELECT` line |
| `--select-indent N` | `select_indent` | `2` | how far the list indents when it starts below `SELECT` |
| `--clause-keyword-align` | `clause_keyword_align` | `left` | root clause keywords flush left, or in a river |
| `--river-gutter N` | `river_gutter` | `6` | the column a river aligns them to |
| `--table-alias-style` | `table_alias_style` | `bare` | `FROM orders o` or `FROM orders AS o` |

Most of what follows is generated from this one query, so you can compare the
outputs directly:

```sql
select cust.email, ord.total, ord.order_date from customers cust join orders ord on ord.customer_id = cust.customer_id left join shipping_addresses addr on addr.order_id = ord.order_id and addr.address_type = 'shipping' where ord.order_date >= '2026-07-01' and cust.segment = 'enterprise';
```

The default output — the baseline for every comparison that follows:

```sql
SELECT cust.email
     , ord.total
     , ord.order_date
FROM customers               cust
JOIN orders                  ord  ON ord.customer_id   = cust.customer_id
LEFT JOIN shipping_addresses addr ON addr.order_id     = ord.order_id
                                 AND addr.address_type = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment    = 'enterprise';
```

### align — turn the padding off

`--no-align` (or `align = false`) keeps sqlalign's line structure and emits one
space between tokens instead of padding into columns. This is what most published
SQL style guides ask for.

```sh
sqlalign --no-align query.sql
```

```sql
SELECT cust.email
     , ord.total
     , ord.order_date
FROM customers cust
JOIN orders ord ON ord.customer_id = cust.customer_id
LEFT JOIN shipping_addresses addr ON addr.order_id = ord.order_id
  AND addr.address_type = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment = 'enterprise';
```

Same lines, same breaks, no padding. Note that the trailing `AND` condition, which
had been right-aligned under `ON`, falls back to a two-space indent.

### align_targets — keep some columns, drop others

`--align-targets` takes a comma-separated list. Anything you leave out collapses
to a single space; `--no-align` is the shorthand for leaving out all six. An
unrecognised name is an error, not a silent no-op.

| Target | Aligns |
|---|---|
| `aliases` | `AS x` in a select list, and table aliases in `FROM`/`JOIN` |
| `operators` | `=`, `!=`, `<`, `LIKE`, `IS` … in `WHERE`/`ON`/`HAVING`/`CASE` |
| `join_conditions` | the `ON`/`AND` column across a whole `FROM` block |
| `case_results` | `THEN` in a short-form `CASE` |
| `column_types` | column types in `CREATE TABLE` |
| `column_constraints` | `NOT NULL`/`DEFAULT`, and Redshift `ENCODE` |

Keeping only the alias column:

```sh
sqlalign --align-targets aliases query.sql
```

```sql
SELECT cust.email
     , ord.total
     , ord.order_date
FROM customers               cust
JOIN orders                  ord ON ord.customer_id = cust.customer_id
LEFT JOIN shipping_addresses addr ON addr.order_id = ord.order_id
  AND addr.address_type = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment = 'enterprise';
```

The remaining examples need a query with more columns in play:

```sql
select o.order_id, sum(o.total) as revenue, count(*) as order_count, case when sum(o.total) >= 1000 then 'large' when sum(o.total) > 0 then 'small' else 'none' end as bucket from orders o join customers cust on cust.id = o.customer_id where o.status = 'complete' and o.channel = 'web' group by o.order_id;
```

Keeping only the operator column:

```sh
sqlalign --align-targets operators wide.sql
```

```sql
SELECT o.order_id
     , SUM(o.total) AS revenue
     , COUNT(*) AS order_count
     , CASE WHEN SUM(o.total) >= 1000 THEN 'large'
            WHEN SUM(o.total) > 0 THEN 'small'
            ELSE 'none'
       END AS bucket
FROM orders o
JOIN customers cust ON cust.id = o.customer_id
WHERE o.status  = 'complete'
  AND o.channel = 'web'
GROUP BY o.order_id;
```

The `WHERE` operators still line up; the `AS`, alias and `THEN` columns are gone.
That same query with everything **except** `case_results` shows how narrow a
single target's effect is — only the `THEN` column disappears:

```sh
sqlalign --align-targets aliases,operators,join_conditions,column_types,column_constraints wide.sql
```

```sql
SELECT o.order_id
     , SUM(o.total) AS revenue
     , COUNT(*)     AS order_count
     , CASE WHEN SUM(o.total) >= 1000 THEN 'large'
            WHEN SUM(o.total) > 0 THEN 'small'
            ELSE 'none'
       END          AS bucket
FROM orders    o
JOIN customers cust ON cust.id = o.customer_id
WHERE o.status  = 'complete'
  AND o.channel = 'web'
GROUP BY o.order_id;
```

The two DDL targets work the same way. Run the `CREATE TABLE` from earlier on this
page with `--align-targets column_types` and the type column survives while the
constraint column does not:

```sh
sqlalign --align-targets column_types table.sql
```

```sql
CREATE TABLE daily_revenue (
    report_date DATE NOT NULL
  , channel     VARCHAR(50) NOT NULL
  , revenue     NUMERIC(12, 2) DEFAULT 0
  , order_count INT DEFAULT 0
  , created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  , PRIMARY KEY (report_date, channel)
);
```

and with `--align-targets column_constraints`, the reverse:

```sh
sqlalign --align-targets column_constraints table.sql
```

```sql
CREATE TABLE daily_revenue (
    report_date DATE       NOT NULL
  , channel VARCHAR(50)    NOT NULL
  , revenue NUMERIC(12, 2) DEFAULT 0
  , order_count INT        DEFAULT 0
  , created_at TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
  , PRIMARY KEY (report_date, channel)
);
```

### comma_position — trailing commas

`--comma-position trailing` moves every separator comma to the end of the
preceding line. The item column does not move, and no other alignment changes:
only the comma travels.

```sh
sqlalign --comma-position trailing query.sql
```

```sql
SELECT cust.email,
       ord.total,
       ord.order_date
FROM customers               cust
JOIN orders                  ord  ON ord.customer_id   = cust.customer_id
LEFT JOIN shipping_addresses addr ON addr.order_id     = ord.order_id
                                 AND addr.address_type = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment    = 'enterprise';
```

### boolean_operator_position — trailing AND/OR

`--boolean-operator-position trailing` moves `AND`/`OR` to the end of the line
they follow. Conditions then start under the first condition rather than being
pushed right by the connective.

```sh
sqlalign --boolean-operator-position trailing query.sql
```

```sql
SELECT cust.email
     , ord.total
     , ord.order_date
FROM customers               cust
JOIN orders                  ord  ON ord.customer_id   = cust.customer_id
LEFT JOIN shipping_addresses addr ON addr.order_id     = ord.order_id AND
                                     addr.address_type = 'shipping'
WHERE ord.order_date >= '2026-07-01' AND
      cust.segment    = 'enterprise';
```

This setting and `comma_position` are independent, but the `trailing` preset sets
both at once.

### on_placement — drop ON below the table

`--on-placement own_line` moves each join's `ON` off the table line and onto its
own, right-aligned to end at column 5 like a `WHERE`. The alias column stays, and
the conditions now stack down the left margin instead of hanging off the right.

```sh
sqlalign --on-placement own_line query.sql
```

```sql
SELECT cust.email
     , ord.total
     , ord.order_date
FROM customers               cust
JOIN orders                  ord
   ON ord.customer_id   = cust.customer_id
LEFT JOIN shipping_addresses addr
   ON addr.order_id     = ord.order_id
  AND addr.address_type = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment    = 'enterprise';
```

The condition operators still align across the whole block.

### keyword_case — lowercase keywords

`--keyword-case lower` lowercases keywords, function names and type names. No
setting recases identifiers, aliases or string literals.

```sh
sqlalign --keyword-case lower query.sql
```

```sql
select cust.email
     , ord.total
     , ord.order_date
from customers               cust
join orders                  ord  on ord.customer_id   = cust.customer_id
left join shipping_addresses addr on addr.order_id     = ord.order_id
                                 and addr.address_type = 'shipping'
where ord.order_date >= '2026-07-01'
  and cust.segment    = 'enterprise';
```

Type names go with them — and so do function names, so `SUM(o.total)` renders as
`sum(o.total)` and `COUNT(*)` as `count(*)`:

```sql
create table daily_revenue (
    report_date date           not null
  , channel     varchar(50)    not null
  , revenue     numeric(12, 2) default 0
  , order_count int            default 0
  , created_at  timestamp      default current_timestamp
  , primary key (report_date, channel)
);
```

### Presets

If one of the four presets is close enough, start there and override the rest.
`--preset NAME` on the command line, or `preset = "name"` in a config file.

| Preset | Sets |
|---|---|
| `house` | nothing — the default style on this page |
| `compact` | `align = false` |
| `trailing` | `comma_position = "trailing"`, `boolean_operator_position = "trailing"` |
| `dbt` | `keyword_case = "lower"`, `comma_position = "trailing"`, `align = false` |

`--preset dbt` on the same query:

```sh
sqlalign --preset dbt query.sql
```

```sql
select cust.email,
       ord.total,
       ord.order_date
from customers cust
join orders ord on ord.customer_id = cust.customer_id
left join shipping_addresses addr on addr.order_id = ord.order_id
  and addr.address_type = 'shipping'
where ord.order_date >= '2026-07-01'
  and cust.segment = 'enterprise';
```

Precedence runs built-in defaults, then preset, then config file, then
command-line flags — so a flag always wins.

### Two settings sqlalign has to choose for you

`--neq-style {!=,<>}` and `--decimal-style {NUMERIC,DECIMAL}` exist because
sqlglot's parser collapses each of those pairs to a single node, erasing which
spelling you wrote. A spelling has to be picked when printing. These are the only
two places sqlalign decides for you; everything the parser preserves is passed
through as written.
