-- #7: subqueries (derived table + scalar + IN)
select u.id, u.email, recent.last_order_date, (select count(*) from support_tickets st where st.user_id = u.id) as ticket_count from users u join (select customer_id, max(order_date) as last_order_date from orders group by customer_id) recent on recent.customer_id = u.id where u.id in (select user_id from subscriptions where plan = 'premium');
