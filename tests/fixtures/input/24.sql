-- #24: create index (postgres)
create index idx_orders_customer ON orders (customer_id);
create unique index idx_orders_cust_date on orders (customer_id, order_date desc) where status != 'cancelled';
