-- #12: nested functions, casts, comments
select user_id, coalesce(nullif(trim(display_name), ''), email) as name, cast(signup_ts as date) as signup_date, /* legacy field, keep until Q4 */ round(lifetime_value::numeric, 2) as ltv -- rounded for reporting
from user_profiles where deleted_at is null;
