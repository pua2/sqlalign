-- #13: multi-character aliases (mixed lengths)
SELECT cust.customer_id
     , cust.email
     , ord.order_id
     , ord.total
     , line_items.product_id
     , line_items.quantity
     , addr.city
FROM customers               cust
INNER JOIN orders            ord        ON ord.customer_id     = cust.customer_id
LEFT JOIN order_line_items   line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr       ON addr.order_id       = ord.order_id
                                       AND addr.address_type   = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment    = 'enterprise';
