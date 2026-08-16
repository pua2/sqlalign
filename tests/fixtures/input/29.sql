-- #29: tsql batches separated by GO
truncate table staging;
GO
insert into staging (a, b) select a, b from source where a is not null;
GO
