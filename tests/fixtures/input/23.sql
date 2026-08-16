-- #23: truncate
truncate table staging_daily_revenue;
truncate table staging_orders restart identity cascade;
