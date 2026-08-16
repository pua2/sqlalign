-- #18: create materialized view
create materialized view mv_channel_revenue as select channel, date_trunc('week', order_date) as week, sum(total) as revenue, count(*) as order_count from orders group by channel, date_trunc('week', order_date);
