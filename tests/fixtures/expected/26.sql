-- #26: tsql select with TOP and bracketed identifiers
SELECT TOP 10 [Order Id]
     , cust.[Full Name]
     , ord.total
FROM [Sales Orders]  ord
INNER JOIN customers cust ON cust.id = ord.customer_id
WHERE ord.status = 'complete'
  AND ord.total  > 100
ORDER BY ord.total DESC;
