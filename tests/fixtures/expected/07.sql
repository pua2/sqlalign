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
