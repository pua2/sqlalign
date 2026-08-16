-- #15: create table (redshift: encode/distkey/sortkey)
CREATE TABLE fact_orders (
    order_id    BIGINT NOT NULL
  , customer_id BIGINT NOT NULL ENCODE az64
  , order_date  DATE   NOT NULL ENCODE az64
  , total       NUMERIC(12, 2)  ENCODE az64
  , channel     VARCHAR(32)     ENCODE lzo
)
DISTSTYLE KEY DISTKEY (customer_id)
COMPOUND SORTKEY (order_date, customer_id);
