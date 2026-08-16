-- #20: create procedure (dollar-quoted plpgsql body)
CREATE PROCEDURE refresh_daily_revenue(target_date DATE)
LANGUAGE plpgsql
AS $$

DECLARE row_count INT;

BEGIN

DELETE FROM daily_revenue
WHERE report_date = target_date;

INSERT INTO daily_revenue
(  report_date
 , channel
 , revenue
 , order_count)
SELECT order_date
     , channel
     , SUM(total)
     , COUNT(*)
FROM orders
WHERE order_date = target_date
GROUP BY order_date
       , channel;

GET DIAGNOSTICS row_count = ROW_COUNT;

IF row_count = 0
  THEN RAISE WARNING 'no orders found for %', target_date;
  ELSE RAISE NOTICE 'loaded % rows', row_count;
END IF;

END;
$$;
