-- #23: truncate
TRUNCATE TABLE staging_daily_revenue;
TRUNCATE TABLE staging_orders
RESTART IDENTITY CASCADE;
