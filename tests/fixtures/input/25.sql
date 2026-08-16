-- #25: grant
grant select on daily_revenue to reporting_role;
grant select, insert, update, delete on daily_revenue to etl_role;
grant usage on schema analytics to reporting_role;
grant select on all tables in schema analytics to readonly_user;
