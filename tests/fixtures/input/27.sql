-- #27: tsql create table (nvarchar, decimal, primary key)
create table daily_revenue (report_id int not null, report_date date not null, channel nvarchar(50) not null, revenue decimal(12,2), primary key (report_id));
