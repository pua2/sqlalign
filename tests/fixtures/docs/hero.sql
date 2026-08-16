select cust.customer_id, cust.email,
ord.total, addr.city
from customers cust
inner join orders ord
on ord.customer_id = cust.customer_id
left join shipping_addresses addr
on addr.order_id = ord.order_id
and addr.address_type = 'shipping'
where ord.order_date >= '2026-07-01'
and cust.segment = 'enterprise';
