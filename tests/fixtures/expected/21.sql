-- #21 long line in select
SELECT total
     , COALESCE(SUM(CASE WHEN ledger_event_type > 0.00
                          AND ledger_event_type < 10.00
                           THEN ledger_event_type
                     END), 0.00) AS funding_amt
FROM orders;
