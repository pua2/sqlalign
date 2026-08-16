-- #8: case expressions
select order_id, total, case when total >= 1000 then 'large' when total >= 100 then 'medium' when total > 0 then 'small' else 'invalid' end as order_size, case when status = 'complete' then 1 when status = 'pending' and not is_archived then 2 else 99 end as status_rank from orders;
