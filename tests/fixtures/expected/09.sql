-- #9: window functions
SELECT customer_id
     , order_id
     , order_date
     , total
     , ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC) AS rn
     , SUM(total) OVER (PARTITION BY customer_id ORDER BY order_date
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)    AS running_total
     , LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date)   AS prev_order_date
FROM orders;
