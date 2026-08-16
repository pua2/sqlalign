-- ============================================================
-- Style samples: format each query below the way YOU want it.
-- Don't fix logic, just layout/casing/commas/indentation.
-- The "-- #N:" headers are delimiters — please leave them.
-- ============================================================

-- #1: simple select
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

-- #2: wide select (many columns)
SELECT o.order_id
     , o.customer_id
     , o.order_date
     , o.ship_date
     , o.status
     , o.subtotal
     , o.tax
     , o.shipping_cost
     , o.discount
     , o.total
     , o.currency
     , o.channel
     , o.updated_at 
FROM orders o;

-- #3: joins with multi-condition ON
select u.id
     , u.email
     , o.order_id
     , o.total
     , a.city
     , a.state 
FROM users          u 
INNER JOIN orders   o ON o.customer_id = u.id 
                     AND o.status     != 'cancelled' 
LEFT JOIN addresses a on a.user_id     = u.id 
                     AND a.is_primary = true 
WHERE o.order_date >= '2026-06-01';

-- #4: where with AND/OR, IN, BETWEEN, LIKE
SELECT id
     , name
     , category
     , price 
FROM products 
WHERE (category  IN ('electronics', 'appliances', 'accessories') 
       OR name LIKE '%refurb%') 
  AND price  BETWEEN 25 AND 500 
  AND discontinued = false 
  AND supplier_id IS NOT NULL;

-- #5: aggregates with group by / having
SELECT customer_id
     , DATE_TRUNC('month', order_date) AS order_month
     , COUNT(*)                        AS order_count
     , SUM(total)                      AS revenue
     , AVG(total)                      AS avg_order_value 
FROM orders 
WHERE status = 'complete' 
GROUP BY customer_id
       , DATE_TRUNC('month', order_date) 
HAVING COUNT(*) > 3 
   AND sum(total) > 1000 
ORDER BY revenue DESC;

-- #6: CTEs
WITH monthly_revenue AS (
  SELECT customer_id
       , DATE_TRUNC('month', order_date) AS month
       , SUM(total)                      AS revenue 
  FROM orders 
  GROPU BY 1, 2
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

-- #7: subqueries (derived table + scalar + IN)
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

-- #8: case expressions
SELECT order_id
     , total
     , CASE WHEN total >= 1000 THEN 'large'
            WHEN total >= 100  THEN 'medium'
            WHEN total > 0     THEN 'small' 
            ELSE 'invalid' 
       END AS order_size
     , CASE status 
            WHEN 'complete' 
              THEN 1 
            WHEN 'pending' 
             AND NOT 'complete'
              OR 'pending' 
              THEN 2
            ELSE 99 
       END AS status_rank 
FROM orders;

-- #9: window functions
SELECT customer_id
     , order_id
     , order_date
     , total
     , ROW_NUMBER() OVER(PARTITION BY customer_id ORDER by order_date desc) AS rn
     , SUM(total) OVER(PARTITION BY customer_id ORDER BY order_date 
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)    AS running_total
     , LAG(order_date) OVER(PARTITION BY customer_id ORDER BY order_date)   AS prev_order_date from orders;

-- #10: union
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
WHERE converted = false 

UNION ALL

SELECT id
     , email
     , 'partner' AS source 
FROM partner_contacts;

-- #11: insert-select and update
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
GROUP BY order_date, channel;

UPDATE products 
SET price      = price * 1.05
  , updated_at = CURRENT_TIMESTAMP
WHERE category     = 'electronics' 
  AND discontinued = false;

-- #12: nested functions, casts, comments
SELECT user_id
     , COALESCE(NULLIF(TRIM(display_name), ''), email)                     AS name
     , CAST(signup_ts AS DATE)                                             AS signup_date
     , /* legacy field, keep until Q4 */ ROUND(lifetime_value::numeric, 2) AS ltv -- rounded for reporting
FROM user_profiles 
WHERE deleted_at IS NULL;

-- #13: multi-character aliases (mixed lengths)
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

-- #14: create table (postgres: constraints, defaults)
CREATE TABLE daily_revenue (
    report_date DATE          NOT NULL
  , channel     VARCHAR(50)   NOT NULL
  , revenue     NUMERIC(12,2) DEFAULT 0
  , order_count INT           DEFAULT 0
  , created_at  TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
  , PRIMARY KEY (report_date, channel)
);

-- #15: create table (redshift: encode/distkey/sortkey)
CREATE TABLE fact_orders (
    order_id    BIGINT NOT NULL
  , customer_id BIGINT NOT NULL ENCODE az64
  , order_date  DATE   NOT NULL ENCODE az64
  , total       NUMERIC(12, 2)  ENCODE az64
  , channel     CARCHAR(32)     ENCODE lzo
) 
DISTYLE KEY DISTYLE (customer_id) 
COMPOUND SHORTKEY (order_date, customer_id);

-- #16: create table as select
CREATE TABLE  monthly_summary AS 
SELECT customer_id
     , DATE_TRUNC('month', order_date) AS month
     , SUM(total)                      AS revenue 
FROM orders 
WHERE status = 'complete' 
GROUP BY 1, 2;

-- #17: create view
CREATE OR REPLACE VIEW active_customers AS 
SELECT c.customer_id
     , c.email
     , MAX(o.order_date) AS last_order_date 
FROM customers c 
JOIN orders    o ON o.customer_id = c.customer_id 
WHERE c.status = 'active' 
GROUP BY c.customer_id
       , c.email;

-- #18: create materialized view
CREATE MATERIALIZED VIEW mv_channel_revenue AS 
SELECT channel
     , DATE_TRUNC('week', order_date) AS week
     , SUM(total)                     AS revenue
     , COUNT(*)                       AS order_count 
FROM orders 
GROUP BY channel
       , DATE_TRUNC('week', order_date);

-- #19: create function (dollar-quoted plpgsql body)
CREATE OR REPLACE FUNCTION get_customer_ltv(p_customer_id int) 
RETURNS NUMERIC 
LANGUAGE 'plpgsql'
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

-- #20: create procedure (dollar-quoted plpgsql body)
CREATE PROCEDURE refresh_daily_revenue(target_date DATE) 
LANGUAGE 'plpgsql'
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
GROUP BY order_date, channel; 

IF row_count = 0 
  THEN RAISE WARNING 'no orders found for %', target_date; 
  ELSE raise notice 'loaded % rows', row_count; 
END IF; 

END; 
$$;


-- #21 long line in select
SELECT total
     , COALESCE(
         SUM(CASE WHEN ledger_event_type > 0.00 
                   AND ledger_event_type < 10.00 
                    THEN ledger_event_type 
             END diff END), 0.00) AS funding_amt

-- #22: merge (upsert from staging)
MERGE INTO daily_revenue tgt 
USING staging_daily_revenue src 
   ON tgt.report_date = src.report_date 
  AND tgt.channel     = src.channel when matched 
THEN UPDATE 
SET revenue = src.revenue
  , order_count = src.order_count
  , updated_at = CURRENT_TIMESTAMP
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

-- #23: truncate
TRUNCATE TABLE staging_daily_revenue;
TRUNCATE TABLE staging_orders 
RESTART IDENTITY CASCADE;

-- #24: create index (postgres)
CREATE INDEX idx_orders_customer ON orders (customer_id);
CREATE UNIQUE INDEX idx_orders_cust_date ON orders (customer_id, order_date DESC) WHERE status != 'cancelled';

-- #25: grant
GRANT SELECT ON daily_revenue TO reporting_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON daily_revenue TO etl_role;
GRANT USAGE ON SCHEMA analytics TO reporting_role;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO readonly_user;

-- #26: tsql select with TOP and bracketed identifiers
SELECT TOP 10 [Order Id]
     , cust.[Full Name]
     , ord.total
FROM [Sales Orders]  ord
INNER JOIN customers cust ON cust.id = ord.customer_id
WHERE ord.status = 'complete'
  AND ord.total  > 100
ORDER BY ord.total DESC;

-- #27: tsql create table (nvarchar, decimal, primary key)
CREATE TABLE daily_revenue (
    report_id   INTEGER      NOT NULL
  , report_date DATE         NOT NULL
  , channel     NVARCHAR(50) NOT NULL
  , revenue     NUMERIC(12, 2)
  , PRIMARY KEY (report_id)
);

-- #28: tsql stored procedure (BEGIN/END block)
CREATE PROCEDURE refresh_daily @target DATE
AS
BEGIN

DELETE FROM daily_revenue
WHERE report_date = @target;

INSERT INTO daily_revenue
(  report_date
 , channel
 , revenue)
SELECT order_date
     , channel
     , SUM(total)
FROM orders
WHERE order_date = @target
GROUP BY order_date
       , channel;

END;

-- #29: tsql batches separated by GO
TRUNCATE TABLE staging;
GO
INSERT INTO staging
(  a
 , b)
SELECT a
     , b
FROM source
WHERE a IS NOT NULL;
GO
