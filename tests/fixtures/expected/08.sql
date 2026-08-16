-- #8: case expressions
SELECT order_id
     , total
     , CASE WHEN total >= 1000 THEN 'large'
            WHEN total >= 100  THEN 'medium'
            WHEN total > 0     THEN 'small'
            ELSE 'invalid'
       END AS order_size
     , CASE WHEN status = 'complete'
              THEN 1
            WHEN status = 'pending'
             AND NOT is_archived
              THEN 2
            ELSE 99
       END AS status_rank
FROM orders;
