-- #25: grant
GRANT SELECT ON daily_revenue TO reporting_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON daily_revenue TO etl_role;
GRANT USAGE ON SCHEMA analytics TO reporting_role;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO readonly_user;
