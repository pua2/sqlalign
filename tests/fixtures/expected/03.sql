-- #3: joins with multi-condition ON
SELECT u.id
     , u.email
     , o.order_id
     , o.total
     , a.city
     , a.state
FROM users          u
INNER JOIN orders   o ON o.customer_id = u.id
                     AND o.status     != 'cancelled'
LEFT JOIN addresses a ON a.user_id     = u.id
                     AND a.is_primary  = TRUE
WHERE o.order_date >= '2026-06-01';
