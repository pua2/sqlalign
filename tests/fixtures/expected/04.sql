-- #4: where with AND/OR, IN, BETWEEN, LIKE
SELECT id
     , name
     , category
     , price
FROM products
WHERE (category  IN ('electronics', 'appliances', 'accessories')
       OR name LIKE '%refurb%')
  AND price  BETWEEN 25 AND 500
  AND discontinued = FALSE
  AND supplier_id IS NOT NULL;
