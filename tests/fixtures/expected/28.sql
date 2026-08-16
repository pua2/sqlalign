-- #28: tsql stored procedure (BEGIN/END block)
CREATE PROCEDURE refresh_daily @target DATE
AS
BEGIN

DELETE FROM daily_revenue
WHERE report_date = @target;

INSERT INTO daily_revenue
(  report_date
 , channel
 , revenue)
SELECT order_date
     , channel
     , SUM(total)
FROM orders
WHERE order_date = @target
GROUP BY order_date
       , channel;

END;
