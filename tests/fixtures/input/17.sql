-- #17: create view
create or replace view active_customers as select c.customer_id, c.email, max(o.order_date) as last_order_date from customers c join orders o on o.customer_id = c.customer_id where c.status = 'active' group by c.customer_id, c.email;
