-- #3: joins with multi-condition ON
select u.id, u.email, o.order_id, o.total, a.city, a.state from users u inner join orders o on o.customer_id = u.id and o.status != 'cancelled' left join addresses a on a.user_id = u.id and a.is_primary = true where o.order_date >= '2026-06-01';
