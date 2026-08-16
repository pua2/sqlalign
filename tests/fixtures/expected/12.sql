-- #12: nested functions, casts, comments
SELECT user_id
     , COALESCE(NULLIF(TRIM(display_name), ''), email)                     AS name
     , CAST(signup_ts AS DATE)                                             AS signup_date
     , /* legacy field, keep until Q4 */ ROUND(lifetime_value::NUMERIC, 2) AS ltv -- rounded for reporting
FROM user_profiles
WHERE deleted_at IS NULL;
