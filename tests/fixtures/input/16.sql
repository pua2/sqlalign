-- #16: create table as select
create table monthly_summary as select customer_id, date_trunc('month', order_date) as month, sum(total) as revenue from orders where status = 'complete' group by 1, 2;
