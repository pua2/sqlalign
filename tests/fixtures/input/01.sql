-- #1: simple select
select id, first_name, last_name, email, created_at from users where status = 'active' and created_at >= '2026-01-01' order by created_at desc limit 100;
