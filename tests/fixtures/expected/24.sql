-- #24: create index (postgres)
CREATE INDEX idx_orders_customer ON orders (customer_id);
CREATE UNIQUE INDEX idx_orders_cust_date ON orders (customer_id, order_date DESC)
WHERE status != 'cancelled';
