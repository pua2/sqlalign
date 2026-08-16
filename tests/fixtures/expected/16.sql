-- #16: create table as select
CREATE TABLE monthly_summary AS
SELECT customer_id
     , DATE_TRUNC('month', order_date) AS month
     , SUM(total)                      AS revenue
FROM orders
WHERE status = 'complete'
GROUP BY 1, 2;
