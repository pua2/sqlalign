-- #6: CTEs
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
