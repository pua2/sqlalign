-- #1: simple select
SELECT id
     , first_name
     , last_name
     , email
     , created_at
FROM users
WHERE status      = 'active'
  AND created_at >= '2026-01-01'
ORDER BY created_at DESC
LIMIT 100;
