-- #5: aggregates with group by / having
select customer_id, date_trunc('month', order_date) as order_month, count(*) as order_count, sum(total) as revenue, avg(total) as avg_order_value from orders where status = 'complete' group by customer_id, date_trunc('month', order_date) having count(*) > 3 and sum(total) > 1000 order by revenue desc;
