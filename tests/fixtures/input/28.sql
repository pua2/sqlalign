-- #28: tsql stored procedure (BEGIN/END block)
create procedure refresh_daily @target date as begin delete from daily_revenue where report_date = @target; insert into daily_revenue (report_date, channel, revenue) select order_date, channel, sum(total) from orders where order_date = @target group by order_date, channel; end
