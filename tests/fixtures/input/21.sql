-- #21 long line in select
select total, coalesce(sum(case when ledger_event_type > 0.00 and ledger_event_type < 10.00 then ledger_event_type end), 0.00) as funding_amt from orders;
