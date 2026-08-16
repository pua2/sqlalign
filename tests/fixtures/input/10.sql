-- #10: union
select id, email, 'customer' as source from customers where created_at >= '2026-01-01' union all select id, email, 'lead' as source from leads where converted = false union all select id, email, 'partner' as source from partner_contacts;
