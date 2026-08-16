-- #27: tsql create table (nvarchar, decimal, primary key)
CREATE TABLE daily_revenue (
    report_id   INTEGER      NOT NULL
  , report_date DATE         NOT NULL
  , channel     NVARCHAR(50) NOT NULL
  , revenue     NUMERIC(12, 2)
  , PRIMARY KEY (report_id)
);
