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
