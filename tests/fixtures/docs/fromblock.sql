select 1 from customers cust
inner join orders ord on ord.customer_id = cust.id
left join order_lines lines on lines.order_id = ord.id
left join addresses addr on addr.order_id = ord.id
and addr.kind = 'shipping';
