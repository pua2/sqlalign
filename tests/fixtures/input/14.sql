-- #14: create table (postgres: constraints, defaults)
create table daily_revenue (report_date date not null, channel varchar(50) not null, revenue numeric(12,2) default 0, order_count int default 0, created_at timestamp default current_timestamp, primary key (report_date, channel));
