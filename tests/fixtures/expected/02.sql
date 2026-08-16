-- #2: wide select (many columns)
SELECT o.order_id
     , o.customer_id
     , o.order_date
     , o.ship_date
     , o.status
     , o.subtotal
     , o.tax
     , o.shipping_cost
     , o.discount
     , o.total
     , o.currency
     , o.channel
     , o.updated_at
FROM orders o;
