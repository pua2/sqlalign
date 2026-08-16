-- #18: create materialized view
CREATE MATERIALIZED VIEW mv_channel_revenue AS
SELECT channel
     , DATE_TRUNC('week', order_date) AS week
     , SUM(total)                     AS revenue
     , COUNT(*)                       AS order_count
FROM orders
GROUP BY channel
       , DATE_TRUNC('week', order_date);
