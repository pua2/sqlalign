-- #26: tsql select with TOP and bracketed identifiers
select top 10 [Order Id], cust.[Full Name], ord.total from [Sales Orders] ord inner join customers cust on cust.id = ord.customer_id where ord.status = 'complete' and ord.total > 100 order by ord.total desc;
