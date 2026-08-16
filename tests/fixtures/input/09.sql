-- #9: window functions
select customer_id, order_id, order_date, total, row_number() over (partition by customer_id order by order_date desc) as rn, sum(total) over (partition by customer_id order by order_date rows between unbounded preceding and current row) as running_total, lag(order_date) over (partition by customer_id order by order_date) as prev_order_date from orders;
