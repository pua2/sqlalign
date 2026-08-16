-- #10: union
SELECT id
     , email
     , 'customer' AS source
FROM customers
WHERE created_at >= '2026-01-01'

UNION ALL

SELECT id
     , email
     , 'lead' AS source
FROM leads
WHERE converted = FALSE

UNION ALL

SELECT id
     , email
     , 'partner' AS source
FROM partner_contacts;
