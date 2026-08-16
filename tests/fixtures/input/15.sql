-- #15: create table (redshift: encode/distkey/sortkey)
create table fact_orders (order_id bigint not null, customer_id bigint not null encode az64, order_date date not null encode az64, total numeric(12,2) encode az64, channel varchar(32) encode lzo) diststyle key distkey (customer_id) compound sortkey (order_date, customer_id);
