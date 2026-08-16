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
GROUP BY order_date
       , channel;

UPDATE products
SET price      = price * 1.05
  , updated_at = CURRENT_TIMESTAMP
WHERE category     = 'electronics'
  AND discontinued = FALSE;
