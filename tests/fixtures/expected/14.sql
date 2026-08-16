-- #14: create table (postgres: constraints, defaults)
CREATE TABLE daily_revenue (
    report_date DATE           NOT NULL
  , channel     VARCHAR(50)    NOT NULL
  , revenue     NUMERIC(12, 2) DEFAULT 0
  , order_count INT            DEFAULT 0
  , created_at  TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
  , PRIMARY KEY (report_date, channel)
);
