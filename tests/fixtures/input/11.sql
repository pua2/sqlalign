-- #11: insert-select and update
insert into daily_revenue (report_date, channel, revenue, order_count) select order_date, channel, sum(total), count(*) from orders where order_date = current_date - 1 group by order_date, channel;
update products set price = price * 1.05, updated_at = current_timestamp where category = 'electronics' and discontinued = false;
