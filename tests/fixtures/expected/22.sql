-- #22: merge (upsert from staging)
MERGE INTO daily_revenue tgt
USING staging_daily_revenue src
   ON tgt.report_date = src.report_date
  AND tgt.channel     = src.channel
WHEN MATCHED
THEN UPDATE
SET revenue     = src.revenue
  , order_count = src.order_count
  , updated_at  = CURRENT_TIMESTAMP
WHEN NOT MATCHED
THEN INSERT
(  report_date
 , channel
 , revenue
 , order_count)
VALUES
(  src.report_date
 , src.channel
 , src.revenue
 , src.order_count
);
