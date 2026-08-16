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
HAVING COUNT(*)   > 3
   AND SUM(total) > 1000
ORDER BY revenue DESC;
